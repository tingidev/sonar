"""Unit tests for the LLM-as-judge description quality evaluator."""

from __future__ import annotations

import asyncio
import json

from sonar.connectors.types import Column, Table
from sonar.engine.describe import (
    ColumnDescription,
    PIIRisk,
    SemanticType,
    TableDescription,
)
from sonar.engine.llm import LLMClient
from sonar.eval.descriptions import evaluate_descriptions
from sonar.index.bundle import BundleMeta, ContextBundle


class FakeJudgeClient(LLMClient):
    def __init__(self, response_for):
        self._response_for = response_for
        self.calls: list[str] = []
        self._lock = asyncio.Lock()

    async def generate(self, prompt: str, system: str | None = None) -> str:
        async with self._lock:
            self.calls.append(prompt)
        value = self._response_for(prompt, system)
        if isinstance(value, BaseException):
            raise value
        return value


def _judge_payload(
    accuracy: int = 5,
    specificity: int = 5,
    domain_inference: int = 5,
    *,
    reasoning: str = "ok",
) -> str:
    return json.dumps(
        {
            "accuracy": {"score": accuracy, "reasoning": reasoning},
            "specificity": {"score": specificity, "reasoning": reasoning},
            "domain_inference": {"score": domain_inference, "reasoning": reasoning},
        }
    )


def _table(name: str) -> Table:
    return Table(
        schema="public",
        name=name,
        columns=(Column("id", "int", False, True),),
    )


def _description(name: str) -> TableDescription:
    cols = (
        ColumnDescription(
            name="id",
            description="pk",
            semantic_type=SemanticType.IDENTIFIER,
            pii_risk=PIIRisk.NONE,
            confidence=0.9,
        ),
    )
    return TableDescription(
        schema="public",
        name=name,
        description="d",
        grain="g",
        domain_hints=(),
        columns=cols,
        confidence=0.9,
    )


def _bundle(
    tables: list[Table],
    descriptions: dict[tuple[str, str], TableDescription | None],
) -> ContextBundle:
    meta = BundleMeta(
        schema_version=1,
        generated_at="2026-01-01T00:00:00Z",
        connector="postgres",
        database="x",
    )
    return ContextBundle(
        meta=meta,
        tables=tuple(tables),
        descriptions=descriptions,
        relationships=(),
    )


class TestEvaluateDescriptions:
    async def test_high_scores_aggregate(self) -> None:
        bundle = _bundle(
            [_table("a"), _table("b")],
            {("public", "a"): _description("a"), ("public", "b"): _description("b")},
        )
        client = FakeJudgeClient(lambda prompt, system: _judge_payload(5, 4, 4))
        report = await evaluate_descriptions(bundle, client)
        assert report.scored_count == 2
        assert report.mean_accuracy == 5.0
        assert report.mean_specificity == 4.0
        assert report.mean_domain_inference == 4.0
        assert report.flagged == ()

    async def test_low_score_flagged_on_any_dimension(self) -> None:
        bundle = _bundle([_table("a")], {("public", "a"): _description("a")})
        client = FakeJudgeClient(
            lambda prompt, system: _judge_payload(2, 4, 4, reasoning="off-topic")
        )
        report = await evaluate_descriptions(bundle, client)
        assert len(report.flagged) == 1
        flagged = report.flagged[0]
        assert flagged.accuracy == 2
        assert flagged.accuracy_reasoning == "off-topic"

    async def test_score_of_exactly_three_not_flagged(self) -> None:
        bundle = _bundle([_table("a")], {("public", "a"): _description("a")})
        client = FakeJudgeClient(lambda prompt, system: _judge_payload(3, 3, 3))
        report = await evaluate_descriptions(bundle, client)
        assert report.flagged == ()

    async def test_null_descriptions_skipped(self) -> None:
        bundle = _bundle(
            [_table("a"), _table("b")],
            {("public", "a"): _description("a"), ("public", "b"): None},
        )
        client = FakeJudgeClient(lambda prompt, system: _judge_payload(4, 4, 4))
        report = await evaluate_descriptions(bundle, client)
        assert report.scored_count == 1
        assert report.skipped_null == 1

    async def test_judge_parse_failure_handled(self) -> None:
        bundle = _bundle(
            [_table("a"), _table("b")],
            {("public", "a"): _description("a"), ("public", "b"): _description("b")},
        )

        def respond(prompt: str, system: str | None) -> str:
            if "public.b" in prompt:
                return "not json"
            return _judge_payload(5, 5, 5)

        client = FakeJudgeClient(respond)
        report = await evaluate_descriptions(bundle, client)
        assert report.scored_count == 1
        assert report.total_judge_failures == 1

    async def test_judge_provider_error_handled(self) -> None:
        bundle = _bundle([_table("a")], {("public", "a"): _description("a")})
        client = FakeJudgeClient(lambda prompt, system: RuntimeError("api down"))
        report = await evaluate_descriptions(bundle, client)
        assert report.scored_count == 0
        assert report.total_judge_failures == 1
        assert report.mean_accuracy == 0.0

    async def test_all_tables_parse_fail_real_path(self) -> None:
        """Real `_parse_score` path: every table returns non-JSON; report has zero scored."""
        bundle = _bundle(
            [_table("a"), _table("b")],
            {("public", "a"): _description("a"), ("public", "b"): _description("b")},
        )
        client = FakeJudgeClient(lambda prompt, system: "not valid json at all")
        report = await evaluate_descriptions(bundle, client)
        assert report.scored_count == 0
        assert report.total_judge_failures == 2
        assert report.mean_accuracy == 0.0
        assert report.per_table == ()

    async def test_scores_clamped_to_one_through_five(self) -> None:
        bundle = _bundle([_table("a")], {("public", "a"): _description("a")})
        client = FakeJudgeClient(lambda prompt, system: _judge_payload(9, 0, 3))
        report = await evaluate_descriptions(bundle, client)
        score = report.per_table[0]
        assert score.accuracy == 5
        assert score.specificity == 1
        assert score.domain_inference == 3

    async def test_reasoning_captured_per_dimension(self) -> None:
        bundle = _bundle([_table("a")], {("public", "a"): _description("a")})

        def respond(prompt: str, system: str | None) -> str:
            return json.dumps(
                {
                    "accuracy": {"score": 4, "reasoning": "matches schema"},
                    "specificity": {"score": 3, "reasoning": "generic phrasing"},
                    "domain_inference": {"score": 5, "reasoning": "domain clear"},
                }
            )

        client = FakeJudgeClient(respond)
        report = await evaluate_descriptions(bundle, client)
        score = report.per_table[0]
        assert score.accuracy_reasoning == "matches schema"
        assert score.specificity_reasoning == "generic phrasing"
        assert score.domain_inference_reasoning == "domain clear"

    async def test_missing_reasoning_defaults_to_empty(self) -> None:
        bundle = _bundle([_table("a")], {("public", "a"): _description("a")})

        def respond(prompt: str, system: str | None) -> str:
            return json.dumps(
                {
                    "accuracy": {"score": 4},
                    "specificity": {"score": 4},
                    "domain_inference": {"score": 4},
                }
            )

        client = FakeJudgeClient(respond)
        report = await evaluate_descriptions(bundle, client)
        score = report.per_table[0]
        assert score.accuracy_reasoning == ""

    async def test_tables_subset_restricts_evaluation(self) -> None:
        bundle = _bundle(
            [_table("a"), _table("b")],
            {("public", "a"): _description("a"), ("public", "b"): _description("b")},
        )
        client = FakeJudgeClient(lambda prompt, system: _judge_payload(5, 5, 5))
        subset = (bundle.tables[0],)
        report = await evaluate_descriptions(bundle, client, tables=subset)
        assert report.scored_count == 1
        assert len(client.calls) == 1
        assert "public.a" in client.calls[0]

    async def test_tables_argument_drives_iteration_order(self) -> None:
        """When `tables` is given, evaluation follows that order, not dict insertion order."""
        bundle = _bundle(
            [_table("a"), _table("b"), _table("c")],
            {
                ("public", "a"): _description("a"),
                ("public", "b"): _description("b"),
                ("public", "c"): _description("c"),
            },
        )
        client = FakeJudgeClient(lambda prompt, system: _judge_payload(5, 5, 5))
        reversed_order = (bundle.tables[2], bundle.tables[0], bundle.tables[1])
        report = await evaluate_descriptions(bundle, client, tables=reversed_order)
        assert [(s.schema, s.name) for s in report.per_table] == [
            ("public", "c"),
            ("public", "a"),
            ("public", "b"),
        ]

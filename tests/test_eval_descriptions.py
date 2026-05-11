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
        client = FakeJudgeClient(
            lambda prompt, system: json.dumps(
                {"accuracy": 0.9, "completeness": 0.85, "specificity": 0.8}
            )
        )
        report = await evaluate_descriptions(bundle, client)
        assert report.scored_count == 2
        assert report.mean_accuracy == 0.9
        assert report.mean_completeness == 0.85
        assert report.flagged == ()

    async def test_low_scores_flagged(self) -> None:
        bundle = _bundle([_table("a")], {("public", "a"): _description("a")})
        client = FakeJudgeClient(
            lambda prompt, system: json.dumps(
                {"accuracy": 0.3, "completeness": 0.7, "specificity": 0.7}
            )
        )
        report = await evaluate_descriptions(bundle, client)
        assert len(report.flagged) == 1
        assert report.flagged[0].accuracy == 0.3

    async def test_null_descriptions_skipped(self) -> None:
        bundle = _bundle(
            [_table("a"), _table("b")],
            {("public", "a"): _description("a"), ("public", "b"): None},
        )
        client = FakeJudgeClient(
            lambda prompt, system: json.dumps(
                {"accuracy": 0.8, "completeness": 0.8, "specificity": 0.8}
            )
        )
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
            return json.dumps({"accuracy": 0.9, "completeness": 0.9, "specificity": 0.9})

        client = FakeJudgeClient(respond)
        report = await evaluate_descriptions(bundle, client)
        assert report.scored_count == 1
        assert report.judge_failures == 1

    async def test_judge_provider_error_handled(self) -> None:
        bundle = _bundle([_table("a")], {("public", "a"): _description("a")})
        client = FakeJudgeClient(lambda prompt, system: RuntimeError("api down"))
        report = await evaluate_descriptions(bundle, client)
        assert report.scored_count == 0
        assert report.judge_failures == 1
        assert report.mean_accuracy == 0.0

    async def test_scores_clamped_to_unit_interval(self) -> None:
        bundle = _bundle([_table("a")], {("public", "a"): _description("a")})
        client = FakeJudgeClient(
            lambda prompt, system: json.dumps(
                {"accuracy": 1.5, "completeness": -0.2, "specificity": 0.6}
            )
        )
        report = await evaluate_descriptions(bundle, client)
        assert report.per_table[0].accuracy == 1.0
        assert report.per_table[0].completeness == 0.0
        assert report.per_table[0].specificity == 0.6

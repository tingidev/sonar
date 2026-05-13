"""LLM-as-judge description quality evaluation."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

from sonar.connectors.types import Table
from sonar.engine.describe import TableDescription
from sonar.engine.llm import LLMClient, LLMConfig
from sonar.eval._prompts import JUDGE_SYSTEM_PROMPT, build_judge_prompt
from sonar.index.bundle import ContextBundle

_LOGGER = logging.getLogger("sonar.eval.descriptions")

_LOW_THRESHOLD = 3
_SCORE_MIN = 1
_SCORE_MAX = 5


@dataclass(frozen=True)
class TableScore:
    schema: str
    name: str
    accuracy: int
    specificity: int
    domain_inference: int
    accuracy_reasoning: str
    specificity_reasoning: str
    domain_inference_reasoning: str


@dataclass(frozen=True)
class DescriptionQualityReport:
    scored_count: int
    skipped_null: int
    # Sum of provider errors (LLM call raised) and parse errors (response wasn't
    # valid JSON in the expected shape). Kept as one number because both
    # outcomes share the same caller semantics: the table contributes nothing
    # to aggregate metrics and the CLI reports a non-fatal failure.
    total_judge_failures: int
    mean_accuracy: float
    mean_specificity: float
    mean_domain_inference: float
    flagged: tuple[TableScore, ...]
    per_table: tuple[TableScore, ...]


async def evaluate_descriptions(
    bundle: ContextBundle,
    llm_client: LLMClient,
    *,
    config: LLMConfig | None = None,
    tables: tuple[Table, ...] | None = None,
) -> DescriptionQualityReport:
    """Score description quality.

    `tables`, when provided, restricts evaluation to that subset (matched by
    schema+name against the bundle). When omitted, all tables with a non-null
    description in the bundle are scored.
    """
    config = config or LLMConfig()
    semaphore = asyncio.Semaphore(config.max_concurrent_calls)

    scored_targets: list[tuple[Table, TableDescription]] = []
    skipped_null = 0
    if tables is not None:
        for table in tables:
            description = bundle.descriptions.get((table.schema, table.name))
            if description is None:
                skipped_null += 1
                continue
            scored_targets.append((table, description))
    else:
        table_by_key = {(t.schema, t.name): t for t in bundle.tables}
        for key, description in bundle.descriptions.items():
            if description is None:
                skipped_null += 1
                continue
            table = table_by_key.get(key)
            if table is None:
                continue
            scored_targets.append((table, description))

    async def _score(target: tuple[Table, TableDescription]) -> TableScore | None:
        table, description = target
        async with semaphore:
            try:
                raw = await llm_client.generate(
                    build_judge_prompt(table, description), system=JUDGE_SYSTEM_PROMPT
                )
            except Exception:  # noqa: BLE001 - judge boundary
                _LOGGER.info(
                    "judge_call",
                    extra={
                        "schema": table.schema,
                        "table": table.name,
                        "outcome": "provider_error",
                    },
                )
                return None
        return _parse_score(raw, table)

    results = await asyncio.gather(*(_score(t) for t in scored_targets))

    per_table: list[TableScore] = [r for r in results if r is not None]
    total_judge_failures = sum(1 for r in results if r is None)

    if per_table:
        mean_a = sum(s.accuracy for s in per_table) / len(per_table)
        mean_s = sum(s.specificity for s in per_table) / len(per_table)
        mean_d = sum(s.domain_inference for s in per_table) / len(per_table)
    else:
        mean_a = mean_s = mean_d = 0.0

    flagged = tuple(
        s
        for s in per_table
        if s.accuracy < _LOW_THRESHOLD
        or s.specificity < _LOW_THRESHOLD
        or s.domain_inference < _LOW_THRESHOLD
    )

    return DescriptionQualityReport(
        scored_count=len(per_table),
        skipped_null=skipped_null,
        total_judge_failures=total_judge_failures,
        mean_accuracy=mean_a,
        mean_specificity=mean_s,
        mean_domain_inference=mean_d,
        flagged=flagged,
        per_table=tuple(per_table),
    )


def _parse_score(raw: str, table: Table) -> TableScore | None:
    try:
        payload = json.loads(raw)
        accuracy_score, accuracy_reasoning = _extract_dimension(payload, "accuracy")
        specificity_score, specificity_reasoning = _extract_dimension(payload, "specificity")
        domain_score, domain_reasoning = _extract_dimension(payload, "domain_inference")
        score = TableScore(
            schema=table.schema,
            name=table.name,
            accuracy=accuracy_score,
            specificity=specificity_score,
            domain_inference=domain_score,
            accuracy_reasoning=accuracy_reasoning,
            specificity_reasoning=specificity_reasoning,
            domain_inference_reasoning=domain_reasoning,
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        _LOGGER.info(
            "judge_call",
            extra={
                "schema": table.schema,
                "table": table.name,
                "outcome": "parse_error",
            },
        )
        return None
    _LOGGER.info(
        "judge_call",
        extra={"schema": table.schema, "table": table.name, "outcome": "ok"},
    )
    return score


def _extract_dimension(payload: dict, key: str) -> tuple[int, str]:
    """Pull (score, reasoning) for one dimension; clamp score to 1-5."""
    entry = payload[key]
    if not isinstance(entry, dict):
        raise ValueError(f"dimension {key!r} not an object")
    score = _clamp(int(entry["score"]))
    reasoning_raw = entry.get("reasoning", "")
    reasoning = str(reasoning_raw) if reasoning_raw is not None else ""
    return score, reasoning


def _clamp(value: int) -> int:
    if value < _SCORE_MIN:
        return _SCORE_MIN
    if value > _SCORE_MAX:
        return _SCORE_MAX
    return value

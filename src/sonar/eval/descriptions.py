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

_LOW_THRESHOLD = 0.5


@dataclass(frozen=True)
class TableScore:
    schema: str
    name: str
    accuracy: float
    completeness: float
    specificity: float


@dataclass(frozen=True)
class DescriptionQualityReport:
    scored_count: int
    skipped_null: int
    judge_failures: int
    mean_accuracy: float
    mean_completeness: float
    mean_specificity: float
    flagged: tuple[TableScore, ...]
    per_table: tuple[TableScore, ...]


async def evaluate_descriptions(
    bundle: ContextBundle,
    llm_client: LLMClient,
    *,
    config: LLMConfig | None = None,
) -> DescriptionQualityReport:
    config = config or LLMConfig()
    semaphore = asyncio.Semaphore(config.max_concurrent_calls)
    table_by_key = {(t.schema, t.name): t for t in bundle.tables}

    scored_targets: list[tuple[Table, TableDescription]] = []
    skipped_null = 0
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
    judge_failures = sum(1 for r in results if r is None)

    if per_table:
        mean_a = sum(s.accuracy for s in per_table) / len(per_table)
        mean_c = sum(s.completeness for s in per_table) / len(per_table)
        mean_s = sum(s.specificity for s in per_table) / len(per_table)
    else:
        mean_a = mean_c = mean_s = 0.0

    flagged = tuple(
        s
        for s in per_table
        if s.accuracy < _LOW_THRESHOLD
        or s.completeness < _LOW_THRESHOLD
        or s.specificity < _LOW_THRESHOLD
    )

    return DescriptionQualityReport(
        scored_count=len(per_table),
        skipped_null=skipped_null,
        judge_failures=judge_failures,
        mean_accuracy=mean_a,
        mean_completeness=mean_c,
        mean_specificity=mean_s,
        flagged=flagged,
        per_table=tuple(per_table),
    )


def _parse_score(raw: str, table: Table) -> TableScore | None:
    try:
        payload = json.loads(raw)
        score = TableScore(
            schema=table.schema,
            name=table.name,
            accuracy=_clamp(float(payload["accuracy"])),
            completeness=_clamp(float(payload["completeness"])),
            specificity=_clamp(float(payload["specificity"])),
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


def _clamp(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value

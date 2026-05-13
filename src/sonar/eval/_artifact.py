"""Versioned eval artifact: deterministic sample, prompt hash, JSON output."""

from __future__ import annotations

import datetime as _datetime
import hashlib
import inspect
import json
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path

from sonar.connectors.types import Table
from sonar.engine._prompts import SYSTEM_PROMPT, build_table_prompt
from sonar.eval.descriptions import DescriptionQualityReport
from sonar.index.bundle import ContextBundle


def prompt_version_hash() -> str:
    """SHA-256 of the description engine's system prompt + table prompt template.

    `build_table_prompt` is a function, so we hash its source rather than a
    rendered output (which would depend on a sample table). Changing either
    the system prompt or the table prompt function body shifts the hash.
    """
    template = inspect.getsource(build_table_prompt)
    body = (SYSTEM_PROMPT + "\n" + template).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def select_sample_tables(
    bundle: ContextBundle, sample_size: int | None
) -> tuple[Table, ...]:
    """Pick a deterministic subset of tables with non-null descriptions.

    Selection round-robins across schemas: tables are sorted by name within
    each schema, then interleaved across schemas in sorted schema order, until
    `sample_size` tables are reached. When `sample_size` is None or larger
    than available, all describable tables are returned.

    Tables with a null description in the bundle are excluded.
    """
    described = {key for key, desc in bundle.descriptions.items() if desc is not None}
    candidates = [t for t in bundle.tables if (t.schema, t.name) in described]

    by_schema: dict[str, list[Table]] = defaultdict(list)
    for t in candidates:
        by_schema[t.schema].append(t)
    for schema in by_schema:
        by_schema[schema].sort(key=lambda t: t.name)

    ordered = sorted(by_schema.keys())
    interleaved: list[Table] = []
    index = 0
    remaining = sum(len(v) for v in by_schema.values())
    while remaining > 0:
        for schema in ordered:
            tables = by_schema[schema]
            if index < len(tables):
                interleaved.append(tables[index])
                remaining -= 1
        index += 1

    if sample_size is None:
        return tuple(interleaved)
    return tuple(interleaved[:sample_size])


def build_artifact(
    *,
    bundle_dir: str,
    report: DescriptionQualityReport,
    generator_model: str | None,
    judge_model: str,
    evaluated_tables: tuple[Table, ...],
) -> dict:
    """Serialise an eval run to the versioned artifact shape."""
    now = (
        _datetime.datetime.now(_datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    per_table = [_score_with_reasoning(asdict(s)) for s in report.per_table]
    return {
        "schema_version": 1,
        "run_timestamp": now,
        "bundle_dir": bundle_dir,
        "generator_model": generator_model,
        "judge_model": judge_model,
        "prompt_version_hash": prompt_version_hash(),
        "metrics": {
            "scored_count": report.scored_count,
            "skipped_null": report.skipped_null,
            "total_judge_failures": report.total_judge_failures,
            "mean_accuracy": report.mean_accuracy,
            "mean_specificity": report.mean_specificity,
            "mean_domain_inference": report.mean_domain_inference,
            "flagged_count": len(report.flagged),
        },
        "evaluated_tables": [f"{t.schema}.{t.name}" for t in evaluated_tables],
        "per_table": per_table,
    }


def write_artifact(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=False), encoding="utf-8")


def _score_with_reasoning(entry: dict) -> dict:
    return {
        "schema": entry["schema"],
        "name": entry["name"],
        "accuracy": entry["accuracy"],
        "specificity": entry["specificity"],
        "domain_inference": entry["domain_inference"],
        "accuracy_reasoning": entry["accuracy_reasoning"],
        "specificity_reasoning": entry["specificity_reasoning"],
        "domain_inference_reasoning": entry["domain_inference_reasoning"],
    }

"""Prompts for the LLM-as-judge description quality evaluator."""

from __future__ import annotations

import json

from sonar.connectors.types import Table
from sonar.engine.describe import TableDescription

JUDGE_SYSTEM_PROMPT = """You are an evaluator scoring a generated table description \
on three dimensions, given the table's schema. Return a single JSON object with \
three floats between 0.0 and 1.0. No prose, no markdown, no code fences.

Dimensions:
- accuracy: does the description correctly reflect what the schema shows? Penalise \
claims unsupported by columns or contradicted by column types.
- completeness: are the table's important aspects (grain, key columns, intent) \
covered? Penalise descriptions that ignore obvious structural signals.
- specificity: does the description add domain meaning beyond restating column \
names? Penalise vague or generic prose that any table could share.

A score of 1.0 means the dimension is fully satisfied; 0.5 means borderline; 0.0 \
means the description fails the dimension entirely. Score honestly — do not pad."""


def build_judge_prompt(table: Table, description: TableDescription) -> str:
    qualified = f"{table.schema}.{table.name}"

    column_lines = []
    for col in table.columns:
        parts = [f"{col.name}: {col.data_type}"]
        parts.append(f"nullable={str(col.nullable).lower()}")
        parts.append(f"pk={str(col.is_primary_key).lower()}")
        column_lines.append(f"  - {', '.join(parts)}")
    schema_block = "\n".join(column_lines)

    column_descriptions = []
    for col in description.columns:
        column_descriptions.append(
            {
                "name": col.name,
                "description": col.description,
                "semantic_type": col.semantic_type.value,
                "pii_risk": col.pii_risk.value,
            }
        )

    description_block = json.dumps(
        {
            "description": description.description,
            "grain": description.grain,
            "domain_hints": list(description.domain_hints),
            "columns": column_descriptions,
        },
        indent=2,
    )

    expected = """{
  "accuracy": 0.0,
  "completeness": 0.0,
  "specificity": 0.0
}"""

    return (
        f"Table: {qualified}\n\n"
        f"Schema:\n{schema_block}\n\n"
        f"Generated description (JSON):\n{description_block}\n\n"
        f"Score the description. Return JSON exactly in this shape:\n{expected}"
    )

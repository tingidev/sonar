"""Prompts for the LLM-as-judge description quality evaluator."""

from __future__ import annotations

import json

from sonar.connectors.types import Table
from sonar.engine.describe import TableDescription

JUDGE_SYSTEM_PROMPT = """You are an evaluator scoring a generated table description \
on three dimensions, given the table's schema only. You do NOT see row samples — \
score whether the description is self-contained and useful from schema + description alone.

Return a single JSON object with exactly this shape:
{
  "accuracy": {"score": <int 1-5>, "reasoning": "<one sentence>"},
  "specificity": {"score": <int 1-5>, "reasoning": "<one sentence>"},
  "domain_inference": {"score": <int 1-5>, "reasoning": "<one sentence>"}
}
No prose, no markdown, no code fences.

Dimensions:
- accuracy: does the description correctly reflect what the schema shows? Claims \
must be supported by column names, types, and structural signals.
- specificity: does the description add useful detail beyond restating column \
names? Would this help distinguish this table from others?
- domain_inference: does the description correctly identify the table's domain \
and use appropriate terminology?

Rubric (applies to every dimension):
- 5: strong — fully satisfies the dimension; concrete, well-supported, useful.
- 4: solid — mostly satisfies the dimension with minor gaps or weak phrasing.
- 3: acceptable — partial coverage; defensible but unremarkable.
- 2: weak — significant gaps, vague claims, or noticeable mistakes.
- 1: failing — wrong, generic, or contradicted by the schema.

Score honestly. Use the full 1-5 range; do not cluster around 3 or 5."""


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
  "accuracy": {"score": 0, "reasoning": ""},
  "specificity": {"score": 0, "reasoning": ""},
  "domain_inference": {"score": 0, "reasoning": ""}
}"""

    return (
        f"Table: {qualified}\n\n"
        f"Schema:\n{schema_block}\n\n"
        f"Generated description (JSON):\n{description_block}\n\n"
        f"Score the description. Return JSON exactly in this shape:\n{expected}"
    )

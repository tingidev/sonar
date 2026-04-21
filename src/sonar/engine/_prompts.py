"""Prompt templates for the description engine.

Kept in their own module so they can be inspected and tested independently
and so the system prompt is a stable candidate for Anthropic prompt caching
in a later optimisation pass.
"""

from __future__ import annotations

import json

from sonar.connectors.postgres import Table

SYSTEM_PROMPT = """You are a data semantics analyst.

Your job: given one database table's schema and a handful of sample rows, \
infer what the table represents and classify each column.

Return a single JSON object matching the schema described in the user message. \
Never include prose, markdown, or code fences. No commentary before or after \
the JSON. Be concise.

Classify every column:
- semantic_type: one of
  - "identifier"  - primary or natural key uniquely identifying a row
  - "dimension"   - categorical or descriptive attribute (names, statuses, \
labels, text fields, timestamps used to filter/group by)
  - "measure"     - numeric quantity that is meaningfully aggregated \
(amount, count, total, duration)
  - "other"       - anything that does not fit the three above
- pii_risk: one of
  - "high"  - directly personally identifying (full name, email, phone, \
national ID, credit card, physical address, date of birth)
  - "low"   - quasi-identifying or sensitive in combination (city, age, \
user-agent, IP, free-text comments, device ID)
  - "none"  - no PII signal (surrogate IDs, enums, prices, counts, \
foreign-key surrogates)

Base PII classification on the column name first, then on the shape of the \
sample values. Prefer "none" when unsure for surrogate numeric IDs; prefer \
"high" when the column name or sample values match classic PII tells.

Mark confidence honestly in the 0.0-1.0 range. A value below 0.5 means you \
genuinely could not tell from the evidence given. Do not pad confidence.
"""


def build_table_prompt(table: Table, samples: list[dict]) -> str:
    """Compose the user prompt describing a single table and its samples."""
    qualified_name = f"{table.schema}.{table.name}"

    column_lines = []
    for col in table.columns:
        parts = [f"{col.name}: {col.data_type}"]
        parts.append(f"nullable={str(col.nullable).lower()}")
        parts.append(f"pk={str(col.is_primary_key).lower()}")
        column_lines.append(f"  - {', '.join(parts)}")
    columns_block = "\n".join(column_lines)

    samples_json = json.dumps(samples, default=str, indent=2)

    expected_output = """{
  "description": "1-3 sentences describing what this table represents in business terms.",
  "grain": "One sentence: what does a single row represent?",
  "domain_hints": ["e-commerce", "orders"],
  "columns": [
    {
      "name": "<column name, in the same order as given>",
      "description": "One sentence in business terms.",
      "semantic_type": "identifier | dimension | measure | other",
      "pii_risk": "none | low | high",
      "confidence": 0.0
    }
  ],
  "confidence": 0.0
}"""

    return (
        f"Table: {qualified_name}\n\n"
        f"Columns:\n{columns_block}\n\n"
        f"Sample rows (JSON):\n{samples_json}\n\n"
        f"Return a JSON object with exactly this shape (values replaced, same keys):\n"
        f"{expected_output}\n\n"
        f"The 'columns' array MUST contain one object per input column, in the same order. "
        f"Use the exact enum string values shown for 'semantic_type' and 'pii_risk'."
    )

"""Prompt templates for the description engine.

Kept in their own module so they can be inspected and tested independently
and so the system prompt is a stable candidate for Anthropic prompt caching
in a later optimisation pass.
"""

from __future__ import annotations

import json

from sonar.connectors.types import Table

SYSTEM_PROMPT = """You are a data semantics analyst.

Your job: given one database table's schema and a handful of sample rows, \
infer what the table represents and classify each column.

Return a single JSON object matching the schema described in the user message. \
Never include prose, markdown, or code fences. No commentary before or after \
the JSON. Be concise.

Description quality guidance:
- Anchor claims to evidence the schema makes visible. A downstream reader who \
sees only the schema (no samples) must find the description defensible.
- Expand abbreviated column names when the meaning is clear from context \
(e.g., `pt` -> preferred term, `cd` -> code, `dt` -> date, `ind` -> indicator, \
`amt` -> amount, `qty` -> quantity, `desc` -> description). When unsure, keep \
the abbreviation and describe its likely role.
- Sample rows confirm or refine schema-derived inferences; do not invent \
niche-domain specifics that only the samples reveal. If samples suggest a \
narrow domain (e.g., a specific disease, sport, or product line) but the \
column names and types are domain-neutral, describe the table at the \
schema-supported level and use general domain_hints.
- Be specific without overreaching: prefer "patient identifier" over "unique \
ID" when columns clearly identify patients; avoid "oncology patient \
identifier" unless the schema (column names, types, or table name) makes \
oncology explicit.

Classify every column:
- semantic_type: one of
  - "identifier"  - primary or natural key uniquely identifying a row
  - "dimension"   - categorical or descriptive attribute (names, statuses, \
labels, text fields, timestamps used to filter/group by)
  - "measure"     - numeric quantity that is meaningfully aggregated \
(amount, count, total, duration)
  - "other"       - anything that does not fit the three above
- pii_risk: one of
  - "high"    - directly personally identifying (full name, email, phone, \
national ID, credit card, physical address, date of birth)
  - "medium"  - plausibly identifying when combined with other fields, or \
data the classifier cannot rule out as PII with confidence (free-text \
comments likely to contain names, precise location, device fingerprint, \
IP address, birth year + postal code)
  - "low"     - quasi-identifying or sensitive in combination but weak on \
its own (city, age bracket, coarse timestamp, user-agent family)
  - "none"    - no PII signal (surrogate IDs, enums, prices, counts, \
foreign-key surrogates)

Base PII classification on the column name first, then on the shape of the \
sample values. Prefer "none" when unsure for surrogate numeric IDs; prefer \
"high" when the column name or sample values match classic PII tells. Use \
"medium" when evidence is ambiguous and you cannot confidently rule PII \
out — downstream consumers may treat "medium" as protected alongside "high".

Mark confidence honestly in the 0.0-1.0 range. A value below 0.5 means you \
genuinely could not tell from the evidence given. Do not pad confidence.
"""


_WIDE_TABLE_THRESHOLD = 30


def _compact_samples(samples: list[dict], column_names: list[str]) -> list[dict]:
    """Drop keys whose value is null across every sample row.

    Wide messy schemas (e.g., CMS claims) have many never-populated columns in
    a 5-row sample; stripping them keeps signal density high without hiding
    structure (the full column list is still shown above).
    """
    if not samples:
        return samples
    keep = [c for c in column_names if any(row.get(c) is not None for row in samples)]
    return [{k: row.get(k) for k in keep} for row in samples]


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

    column_names = [col.name for col in table.columns]
    compact = _compact_samples(samples, column_names)
    samples_json = json.dumps(compact, default=str, indent=2)
    is_wide = len(table.columns) >= _WIDE_TABLE_THRESHOLD
    dropped_cols = len(column_names) - (len(compact[0]) if compact else len(column_names))
    samples_note = (
        f" (showing only columns with at least one non-null value across {len(samples)} rows;"
        f" {dropped_cols} all-null columns omitted)"
        if dropped_cols > 0
        else ""
    )

    width_guidance = (
        "\nThis is a wide table (>= 30 columns). Keep each column 'description' "
        "to a short noun phrase (no full sentences) so the JSON fits within the "
        "response budget. Table-level 'description' and 'grain' may be 1-2 sentences.\n"
        if is_wide
        else ""
    )

    expected_output = """{
  "description": "1-3 sentences describing what this table represents in business terms.",
  "grain": "One sentence: what does a single row represent?",
  "domain_hints": ["e-commerce", "orders"],
  "columns": [
    {
      "name": "<column name, in the same order as given>",
      "description": "One sentence in business terms.",
      "semantic_type": "identifier | dimension | measure | other",
      "pii_risk": "none | low | medium | high",
      "confidence": 0.0
    }
  ],
  "confidence": 0.0
}"""

    return (
        f"Table: {qualified_name}\n\n"
        f"Columns:\n{columns_block}\n\n"
        f"Sample rows (JSON){samples_note}:\n{samples_json}\n"
        f"{width_guidance}\n"
        f"Return a JSON object with exactly this shape (values replaced, same keys):\n"
        f"{expected_output}\n\n"
        f"The 'columns' array MUST contain one object per input column, in the same order. "
        f"Use the exact enum string values shown for 'semantic_type' and 'pii_risk'."
    )

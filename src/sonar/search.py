"""Substring search over tables, columns, and descriptions in a ContextBundle."""

from __future__ import annotations

from typing import Any

from sonar.index.bundle import ContextBundle

_MATCH_TIER_TABLE_NAME = 0
_MATCH_TIER_COLUMN_NAME = 1
_MATCH_TIER_DESCRIPTION_BODY = 2


def search_tool(
    bundle: ContextBundle,
    query: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Case-insensitive substring search over tables, columns, and descriptions.

    Ranked: table-name > column-name > description-body. Within a tier,
    alphabetical on `(schema, table)`. Capped at `limit` entries total.
    """
    needle = query.lower()
    if not needle:
        return []

    best_tier: dict[tuple[str, str], int] = {}

    for table in bundle.tables:
        key = (table.schema, table.name)
        if needle in table.name.lower():
            _remember(best_tier, key, _MATCH_TIER_TABLE_NAME)
            continue
        for col in table.columns:
            if needle in col.name.lower():
                _remember(best_tier, key, _MATCH_TIER_COLUMN_NAME)
                break
        description = bundle.descriptions.get(key)
        if description is not None:
            body_texts = [description.description, description.grain]
            body_texts.extend(c.description for c in description.columns)
            if any(needle in (text or "").lower() for text in body_texts):
                _remember(best_tier, key, _MATCH_TIER_DESCRIPTION_BODY)

    ranked = sorted(best_tier.items(), key=lambda item: (item[1], item[0]))

    out: list[dict[str, Any]] = []
    for (schema, name), tier in ranked[:limit]:
        match_type = {
            _MATCH_TIER_TABLE_NAME: "table_name",
            _MATCH_TIER_COLUMN_NAME: "column_name",
            _MATCH_TIER_DESCRIPTION_BODY: "description_body",
        }[tier]
        out.append({"schema": schema, "table": name, "match_type": match_type})
    return out


def _remember(
    best: dict[tuple[str, str], int],
    key: tuple[str, str],
    tier: int,
) -> None:
    current = best.get(key)
    if current is None or tier < current:
        best[key] = tier


__all__ = ["search_tool"]

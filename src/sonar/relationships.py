"""Relationship mapping — unified graph of declared FKs plus naming-heuristic inferences."""

import enum
import logging
import re
from dataclasses import dataclass

from sonar.connectors.postgres import Column, ForeignKey, Table

_LOGGER = logging.getLogger("sonar.relationships")

_STEM_PATTERN = re.compile(r"^(.+)_id$", re.IGNORECASE)


class RelationshipKind(enum.StrEnum):
    DECLARED = "declared"
    INFERRED = "inferred"


@dataclass(frozen=True)
class Relationship:
    source_schema: str
    source_table: str
    source_column: str
    target_schema: str
    target_table: str
    target_column: str
    kind: RelationshipKind


def map_relationships(
    tables: list[Table],
    foreign_keys: list[ForeignKey],
) -> list[Relationship]:
    declared = [
        Relationship(
            source_schema=fk.source_schema,
            source_table=fk.source_table,
            source_column=fk.source_column,
            target_schema=fk.target_schema,
            target_table=fk.target_table,
            target_column=fk.target_column,
            kind=RelationshipKind.DECLARED,
        )
        for fk in foreign_keys
    ]

    declared_source_set: set[tuple[str, str, str]] = {
        (fk.source_schema, fk.source_table, fk.source_column) for fk in foreign_keys
    }

    tables_by_schema_name: dict[tuple[str, str], Table] = {(t.schema, t.name): t for t in tables}

    inferred: list[Relationship] = []
    for table in tables:
        for column in table.columns:
            if column.is_primary_key:
                continue
            triple = (table.schema, table.name, column.name)
            if triple in declared_source_set:
                continue
            stem = _stem_from_name(column.name)
            if stem is None:
                continue
            candidates = _find_candidates(stem, table.schema, tables_by_schema_name)
            if len(candidates) != 1:
                continue
            target_table, target_pk = candidates[0]
            inferred.append(
                Relationship(
                    source_schema=table.schema,
                    source_table=table.name,
                    source_column=column.name,
                    target_schema=target_table.schema,
                    target_table=target_table.name,
                    target_column=target_pk.name,
                    kind=RelationshipKind.INFERRED,
                )
            )

    inferred.sort(key=lambda r: (r.source_schema, r.source_table, r.source_column))

    _LOGGER.info(
        "relationships mapped",
        extra={
            "declared": len(declared),
            "inferred": len(inferred),
            "tables_scanned": len(tables),
        },
    )

    return declared + inferred


def _stem_from_name(col_name: str) -> str | None:
    match = _STEM_PATTERN.match(col_name)
    if match is None:
        return None
    return match.group(1).lower()


def _find_candidates(
    stem: str,
    schema: str,
    tables_by_schema_name: dict[tuple[str, str], Table],
) -> list[tuple[Table, Column]]:
    accepted: list[tuple[Table, Column]] = []
    for table_name in (stem, stem + "s"):
        candidate = tables_by_schema_name.get((schema, table_name))
        if candidate is None:
            continue
        pk = _single_column_pk(candidate)
        if pk is None:
            continue
        if pk.name not in ("id", f"{stem}_id"):
            continue
        accepted.append((candidate, pk))
    return accepted


def _single_column_pk(table: Table) -> Column | None:
    pks = [c for c in table.columns if c.is_primary_key]
    if len(pks) != 1:
        return None
    return pks[0]

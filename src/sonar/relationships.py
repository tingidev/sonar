"""Relationship mapping — unified graph of declared FKs plus naming-heuristic inferences."""

import enum
import logging
from collections import Counter
from dataclasses import dataclass

from sonar.connectors.postgres import Column, ForeignKey, Table

_LOGGER = logging.getLogger("sonar.relationships")

# A PK column whose match-pressure (same-schema non-PK columns matchable to it
# via Rule A or Rule B) exceeds this is excluded as a candidate target — see
# design.md D3.
_CATCH_ALL_PK_THRESHOLD = 15


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

    pk_index = _build_pk_index(tables)
    match_pressure = _build_match_pressure(tables, pk_index)
    excluded_targets = {key for key, p in match_pressure.items() if p > _CATCH_ALL_PK_THRESHOLD}

    inferred: list[Relationship] = []
    for table in tables:
        for column in table.columns:
            if column.is_primary_key:
                continue
            triple = (table.schema, table.name, column.name)
            if triple in declared_source_set:
                continue
            candidates = _candidate_targets(table, column, pk_index, excluded_targets)
            if len(candidates) != 1:
                continue
            (target_schema, target_table), target_pk = candidates[0]
            inferred.append(
                Relationship(
                    source_schema=table.schema,
                    source_table=table.name,
                    source_column=column.name,
                    target_schema=target_schema,
                    target_table=target_table,
                    target_column=target_pk,
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


def _build_pk_index(
    tables: list[Table],
) -> dict[tuple[str, str], list[tuple[str, str]]]:
    index: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for t in tables:
        pks = [c for c in t.columns if c.is_primary_key]
        if len(pks) != 1:
            continue
        index.setdefault((t.schema, pks[0].name), []).append((t.schema, t.name))
    return index


def _build_match_pressure(
    tables: list[Table],
    pk_index: dict[tuple[str, str], list[tuple[str, str]]],
) -> Counter[tuple[str, str]]:
    pressure: Counter[tuple[str, str]] = Counter()
    pk_names_by_schema: dict[str, list[str]] = {}
    for schema, pk_name in pk_index:
        pk_names_by_schema.setdefault(schema, []).append(pk_name)
    for t in tables:
        candidate_pk_names = pk_names_by_schema.get(t.schema, [])
        for c in t.columns:
            if c.is_primary_key:
                continue
            for pk_name in candidate_pk_names:
                if c.name == pk_name or c.name.endswith("_" + pk_name):
                    pressure[(t.schema, pk_name)] += 1
    return pressure


def _candidate_targets(
    table: Table,
    column: Column,
    pk_index: dict[tuple[str, str], list[tuple[str, str]]],
    excluded_targets: set[tuple[str, str]],
) -> list[tuple[tuple[str, str], str]]:
    self_key = (table.schema, table.name)
    candidates: list[tuple[tuple[str, str], str]] = []
    seen: set[tuple[tuple[str, str], str]] = set()

    # Rule A: direct PK-name match. No self_key guard needed — a non-PK column
    # cannot share its own table's PK name, so self-reference is structurally
    # impossible. Rule B's guard below is real because role-prefix matches can
    # legitimately route a column at its own table's PK.
    if (table.schema, column.name) not in excluded_targets:
        for tab in pk_index.get((table.schema, column.name), []):
            entry = (tab, column.name)
            if entry not in seen:
                seen.add(entry)
                candidates.append(entry)

    for (schema, pk_name), tabs in pk_index.items():
        if schema != table.schema:
            continue
        if not column.name.endswith("_" + pk_name):
            continue
        if (schema, pk_name) in excluded_targets:
            continue
        for tab in tabs:
            if tab == self_key:
                continue
            entry = (tab, pk_name)
            if entry not in seen:
                seen.add(entry)
                candidates.append(entry)

    return candidates

"""Relationship recall/precision evaluation against declared FKs."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sonar.connectors.types import ForeignKey, Table
from sonar.eval._types import RelationshipEdge
from sonar.relationships import Relationship, RelationshipKind, map_relationships


def _edge_from_fk(fk: ForeignKey) -> RelationshipEdge:
    return RelationshipEdge(
        source_schema=fk.source_schema,
        source_table=fk.source_table,
        source_column=fk.source_column,
        target_schema=fk.target_schema,
        target_table=fk.target_table,
        target_column=fk.target_column,
    )


def _edge_from_relationship(r: Relationship) -> RelationshipEdge:
    return RelationshipEdge(
        source_schema=r.source_schema,
        source_table=r.source_table,
        source_column=r.source_column,
        target_schema=r.target_schema,
        target_table=r.target_table,
        target_column=r.target_column,
    )


@dataclass(frozen=True)
class TableBreakdown:
    schema: str
    name: str
    declared: int
    inferred_correct: int
    missed: int
    false_positive: int


@dataclass(frozen=True)
class RelationshipReport:
    declared_count: int
    inferred_count: int
    matched_count: int
    recall: float | None
    precision: float | None
    f1: float | None
    missed: tuple[RelationshipEdge, ...]
    false_positive: tuple[RelationshipEdge, ...]
    per_table: tuple[TableBreakdown, ...]


def evaluate_relationships(
    tables: list[Table],
    declared_foreign_keys: list[ForeignKey],
) -> RelationshipReport:
    declared_set = {_edge_from_fk(fk) for fk in declared_foreign_keys}
    inferred = [
        r for r in map_relationships(tables, []) if r.kind is RelationshipKind.INFERRED
    ]
    inferred_set = {_edge_from_relationship(r) for r in inferred}

    matched = declared_set & inferred_set
    missed = declared_set - inferred_set
    false_positive = inferred_set - declared_set

    declared_count = len(declared_set)
    inferred_count = len(inferred_set)
    matched_count = len(matched)

    recall = matched_count / declared_count if declared_count > 0 else None
    precision = matched_count / inferred_count if inferred_count > 0 else None
    f1: float | None
    if recall is None or precision is None or (recall + precision) == 0:
        f1 = None
    else:
        f1 = 2 * recall * precision / (recall + precision)

    per_table = _build_per_table_breakdown(tables, declared_set, matched, missed, false_positive)

    return RelationshipReport(
        declared_count=declared_count,
        inferred_count=inferred_count,
        matched_count=matched_count,
        recall=recall,
        precision=precision,
        f1=f1,
        missed=tuple(sorted(missed, key=_edge_key)),
        false_positive=tuple(sorted(false_positive, key=_edge_key)),
        per_table=per_table,
    )


def _edge_key(edge: RelationshipEdge) -> tuple[str, ...]:
    return (
        edge.source_schema,
        edge.source_table,
        edge.source_column,
        edge.target_schema,
        edge.target_table,
        edge.target_column,
    )


def _build_per_table_breakdown(
    tables: list[Table],
    declared_set: set[RelationshipEdge],
    matched: set[RelationshipEdge],
    missed: set[RelationshipEdge],
    false_positive: set[RelationshipEdge],
) -> tuple[TableBreakdown, ...]:
    declared_by_source: dict[tuple[str, str], int] = defaultdict(int)
    matched_by_source: dict[tuple[str, str], int] = defaultdict(int)
    missed_by_source: dict[tuple[str, str], int] = defaultdict(int)
    fp_by_source: dict[tuple[str, str], int] = defaultdict(int)

    for e in declared_set:
        declared_by_source[(e.source_schema, e.source_table)] += 1
    for e in matched:
        matched_by_source[(e.source_schema, e.source_table)] += 1
    for e in missed:
        missed_by_source[(e.source_schema, e.source_table)] += 1
    for e in false_positive:
        fp_by_source[(e.source_schema, e.source_table)] += 1

    keys = sorted(
        {(t.schema, t.name) for t in tables}
        | declared_by_source.keys()
        | fp_by_source.keys()
    )

    return tuple(
        TableBreakdown(
            schema=schema,
            name=name,
            declared=declared_by_source.get((schema, name), 0),
            inferred_correct=matched_by_source.get((schema, name), 0),
            missed=missed_by_source.get((schema, name), 0),
            false_positive=fp_by_source.get((schema, name), 0),
        )
        for (schema, name) in keys
        if (
            declared_by_source.get((schema, name), 0)
            or matched_by_source.get((schema, name), 0)
            or missed_by_source.get((schema, name), 0)
            or fp_by_source.get((schema, name), 0)
        )
    )

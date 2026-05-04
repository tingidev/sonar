"""Bundle structural diff — compare two ContextBundles by stable keys."""

from __future__ import annotations

from dataclasses import dataclass

from sonar.engine.describe import TableDescription
from sonar.index.bundle import ContextBundle
from sonar.relationships import RelationshipKind


@dataclass(frozen=True)
class DescriptionChange:
    schema: str
    name: str
    text_changed: bool
    confidence_delta: float
    grain_changed: bool
    domain_hints_added: tuple[str, ...]
    domain_hints_removed: tuple[str, ...]


@dataclass(frozen=True)
class RelationshipEdge:
    source_schema: str
    source_table: str
    source_column: str
    target_schema: str
    target_table: str
    target_column: str
    kind: str


@dataclass(frozen=True)
class DiffReport:
    tables_added: tuple[tuple[str, str], ...]
    tables_removed: tuple[tuple[str, str], ...]
    relationships_added: tuple[RelationshipEdge, ...]
    relationships_removed: tuple[RelationshipEdge, ...]
    descriptions_added: tuple[tuple[str, str], ...]
    descriptions_removed: tuple[tuple[str, str], ...]
    descriptions_changed: tuple[DescriptionChange, ...]


def diff_bundles(current: ContextBundle, other: ContextBundle) -> DiffReport:
    current_tables = {(t.schema, t.name) for t in current.tables}
    other_tables = {(t.schema, t.name) for t in other.tables}
    tables_added = tuple(sorted(current_tables - other_tables))
    tables_removed = tuple(sorted(other_tables - current_tables))

    current_rels = {_rel_key(r): r.kind.value for r in current.relationships}
    other_rels = {_rel_key(r): r.kind.value for r in other.relationships}
    rels_added_keys = sorted(set(current_rels) - set(other_rels))
    rels_removed_keys = sorted(set(other_rels) - set(current_rels))

    relationships_added = tuple(
        RelationshipEdge(*key, kind=current_rels[key]) for key in rels_added_keys
    )
    relationships_removed = tuple(
        RelationshipEdge(*key, kind=other_rels[key]) for key in rels_removed_keys
    )

    desc_added: list[tuple[str, str]] = []
    desc_removed: list[tuple[str, str]] = []
    desc_changed: list[DescriptionChange] = []

    all_keys = sorted(set(current.descriptions) | set(other.descriptions))
    for key in all_keys:
        cur = current.descriptions.get(key)
        oth = other.descriptions.get(key)
        if cur is None and oth is not None:
            desc_removed.append(key)
        elif cur is not None and oth is None:
            desc_added.append(key)
        elif cur is not None and oth is not None:
            change = _compare_descriptions(cur, oth)
            if change is not None:
                desc_changed.append(change)

    return DiffReport(
        tables_added=tables_added,
        tables_removed=tables_removed,
        relationships_added=relationships_added,
        relationships_removed=relationships_removed,
        descriptions_added=tuple(desc_added),
        descriptions_removed=tuple(desc_removed),
        descriptions_changed=tuple(desc_changed),
    )


def _rel_key(r: object) -> tuple[str, str, str, str, str, str]:
    return (
        r.source_schema,  # type: ignore[attr-defined]
        r.source_table,  # type: ignore[attr-defined]
        r.source_column,  # type: ignore[attr-defined]
        r.target_schema,  # type: ignore[attr-defined]
        r.target_table,  # type: ignore[attr-defined]
        r.target_column,  # type: ignore[attr-defined]
    )


def _compare_descriptions(
    current: TableDescription, other: TableDescription
) -> DescriptionChange | None:
    text_changed = current.description != other.description
    grain_changed = current.grain != other.grain
    confidence_delta = current.confidence - other.confidence

    cur_hints = set(current.domain_hints)
    oth_hints = set(other.domain_hints)
    added = tuple(sorted(cur_hints - oth_hints))
    removed = tuple(sorted(oth_hints - cur_hints))

    if (
        not text_changed
        and not grain_changed
        and confidence_delta == 0.0
        and not added
        and not removed
    ):
        return None

    return DescriptionChange(
        schema=current.schema,
        name=current.name,
        text_changed=text_changed,
        confidence_delta=confidence_delta,
        grain_changed=grain_changed,
        domain_hints_added=added,
        domain_hints_removed=removed,
    )


__all__ = [
    "DescriptionChange",
    "DiffReport",
    "RelationshipEdge",
    "RelationshipKind",
    "diff_bundles",
]

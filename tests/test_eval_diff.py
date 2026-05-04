"""Unit tests for the bundle diff."""

from __future__ import annotations

from sonar.connectors.types import Column, Table
from sonar.engine.describe import (
    ColumnDescription,
    PIIRisk,
    SemanticType,
    TableDescription,
)
from sonar.eval.diff import diff_bundles
from sonar.index.bundle import BundleMeta, ContextBundle
from sonar.relationships import Relationship, RelationshipKind


def _meta() -> BundleMeta:
    return BundleMeta(
        schema_version=1,
        generated_at="2026-01-01T00:00:00Z",
        connector="postgres",
        database="x",
    )


def _table(name: str) -> Table:
    return Table(
        schema="public",
        name=name,
        columns=(Column("id", "int", False, True),),
    )


def _description(
    name: str,
    *,
    text: str = "d",
    confidence: float = 0.8,
    grain: str = "g",
    hints: tuple[str, ...] = ("test",),
) -> TableDescription:
    cols = (
        ColumnDescription(
            name="id",
            description="pk",
            semantic_type=SemanticType.IDENTIFIER,
            pii_risk=PIIRisk.NONE,
            confidence=0.9,
        ),
    )
    return TableDescription(
        schema="public",
        name=name,
        description=text,
        grain=grain,
        domain_hints=hints,
        columns=cols,
        confidence=confidence,
    )


def _bundle(
    tables: list[Table],
    descriptions: dict[tuple[str, str], TableDescription | None] | None = None,
    relationships: list[Relationship] | None = None,
) -> ContextBundle:
    descriptions = descriptions or {}
    relationships = relationships or []
    return ContextBundle(
        meta=_meta(),
        tables=tuple(tables),
        descriptions=descriptions,
        relationships=tuple(relationships),
    )


def _rel(
    src_table: str,
    src_col: str,
    tgt_table: str,
    tgt_col: str,
    kind: RelationshipKind = RelationshipKind.DECLARED,
) -> Relationship:
    return Relationship(
        source_schema="public",
        source_table=src_table,
        source_column=src_col,
        target_schema="public",
        target_table=tgt_table,
        target_column=tgt_col,
        kind=kind,
    )


class TestDiffBundles:
    def test_identical_bundles_empty_diff(self) -> None:
        a = _bundle(
            [_table("a")],
            {("public", "a"): _description("a")},
        )
        b = _bundle(
            [_table("a")],
            {("public", "a"): _description("a")},
        )
        diff = diff_bundles(a, b)
        assert diff.tables_added == ()
        assert diff.tables_removed == ()
        assert diff.descriptions_changed == ()
        assert diff.relationships_added == ()

    def test_added_table_detected(self) -> None:
        current = _bundle(
            [_table("a"), _table("orders")],
            {
                ("public", "a"): _description("a"),
                ("public", "orders"): _description("orders"),
            },
        )
        other = _bundle([_table("a")], {("public", "a"): _description("a")})
        diff = diff_bundles(current, other)
        assert ("public", "orders") in diff.tables_added
        assert diff.tables_removed == ()

    def test_removed_table_detected(self) -> None:
        current = _bundle([_table("a")], {("public", "a"): _description("a")})
        other = _bundle(
            [_table("a"), _table("legacy")],
            {
                ("public", "a"): _description("a"),
                ("public", "legacy"): _description("legacy"),
            },
        )
        diff = diff_bundles(current, other)
        assert ("public", "legacy") in diff.tables_removed

    def test_confidence_change_detected(self) -> None:
        current = _bundle(
            [_table("a")],
            {("public", "a"): _description("a", confidence=0.85)},
        )
        other = _bundle(
            [_table("a")],
            {("public", "a"): _description("a", confidence=0.72)},
        )
        diff = diff_bundles(current, other)
        assert len(diff.descriptions_changed) == 1
        change = diff.descriptions_changed[0]
        assert abs(change.confidence_delta - (0.85 - 0.72)) < 1e-9
        assert change.text_changed is False

    def test_null_to_present_description(self) -> None:
        current = _bundle([_table("a")], {("public", "a"): _description("a")})
        other = _bundle([_table("a")], {("public", "a"): None})
        diff = diff_bundles(current, other)
        assert ("public", "a") in diff.descriptions_added

    def test_present_to_null_description(self) -> None:
        current = _bundle([_table("a")], {("public", "a"): None})
        other = _bundle([_table("a")], {("public", "a"): _description("a")})
        diff = diff_bundles(current, other)
        assert ("public", "a") in diff.descriptions_removed

    def test_relationship_added_with_kind(self) -> None:
        current = _bundle(
            [_table("a"), _table("b")],
            {
                ("public", "a"): _description("a"),
                ("public", "b"): _description("b"),
            },
            [_rel("a", "id", "b", "id", kind=RelationshipKind.INFERRED)],
        )
        other = _bundle(
            [_table("a"), _table("b")],
            {
                ("public", "a"): _description("a"),
                ("public", "b"): _description("b"),
            },
        )
        diff = diff_bundles(current, other)
        assert len(diff.relationships_added) == 1
        assert diff.relationships_added[0].kind == "inferred"

    def test_text_changed_flag_and_no_text_diff(self) -> None:
        current = _bundle(
            [_table("a")],
            {("public", "a"): _description("a", text="new wording")},
        )
        other = _bundle(
            [_table("a")],
            {("public", "a"): _description("a", text="old wording")},
        )
        diff = diff_bundles(current, other)
        assert len(diff.descriptions_changed) == 1
        assert diff.descriptions_changed[0].text_changed is True

    def test_domain_hint_changes(self) -> None:
        current = _bundle(
            [_table("a")],
            {("public", "a"): _description("a", hints=("test", "added"))},
        )
        other = _bundle(
            [_table("a")],
            {("public", "a"): _description("a", hints=("test", "removed"))},
        )
        diff = diff_bundles(current, other)
        assert len(diff.descriptions_changed) == 1
        change = diff.descriptions_changed[0]
        assert "added" in change.domain_hints_added
        assert "removed" in change.domain_hints_removed

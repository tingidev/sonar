"""Unit tests for the bundle quality report."""

from __future__ import annotations

from sonar.connectors.types import Column, Table
from sonar.engine.describe import (
    ColumnDescription,
    PIIRisk,
    SemanticType,
    TableDescription,
)
from sonar.eval.quality import build_quality_report
from sonar.index.bundle import BundleMeta, ContextBundle
from sonar.relationships import Relationship, RelationshipKind


def _meta() -> BundleMeta:
    return BundleMeta(
        schema_version=1,
        generated_at="2026-01-01T00:00:00Z",
        connector="postgres",
        database="test/db",
    )


def _table(schema: str, name: str, columns: tuple[Column, ...] = ()) -> Table:
    if not columns:
        columns = (Column("id", "int", False, True),)
    return Table(schema=schema, name=name, columns=columns)


def _description(
    schema: str,
    name: str,
    *,
    confidence: float = 0.8,
    pii: dict[str, PIIRisk] | None = None,
) -> TableDescription:
    pii = pii or {}
    cols = (
        ColumnDescription(
            name="id",
            description="Primary key",
            semantic_type=SemanticType.IDENTIFIER,
            pii_risk=pii.get("id", PIIRisk.NONE),
            confidence=confidence,
        ),
    )
    return TableDescription(
        schema=schema,
        name=name,
        description="d",
        grain="g",
        domain_hints=("test",),
        columns=cols,
        confidence=confidence,
    )


def _bundle(
    tables: list[Table],
    descriptions: dict[tuple[str, str], TableDescription | None],
    relationships: list[Relationship],
) -> ContextBundle:
    return ContextBundle(
        meta=_meta(),
        tables=tuple(tables),
        descriptions=descriptions,
        relationships=tuple(relationships),
    )


def _rel(
    src: tuple[str, str, str],
    tgt: tuple[str, str, str],
    kind: RelationshipKind = RelationshipKind.DECLARED,
) -> Relationship:
    return Relationship(
        source_schema=src[0],
        source_table=src[1],
        source_column=src[2],
        target_schema=tgt[0],
        target_table=tgt[1],
        target_column=tgt[2],
        kind=kind,
    )


class TestQualityReport:
    def test_complete_bundle_full_coverage(self) -> None:
        tables = [_table("public", "a"), _table("public", "b")]
        descriptions = {
            ("public", "a"): _description("public", "a"),
            ("public", "b"): _description("public", "b"),
        }
        relationships = [_rel(("public", "a", "id"), ("public", "b", "id"))]
        bundle = _bundle(tables, descriptions, relationships)

        report = build_quality_report(bundle, ".sonar/")

        assert report.table_count == 2
        assert report.description_coverage == 1.0
        assert report.relationship_coverage == 1.0
        assert report.orphan_tables == ()

    def test_partial_descriptions(self) -> None:
        tables = [_table("public", f"t{i}") for i in range(5)]
        descriptions: dict[tuple[str, str], TableDescription | None] = {
            ("public", "t0"): _description("public", "t0"),
            ("public", "t1"): _description("public", "t1"),
            ("public", "t2"): _description("public", "t2"),
            ("public", "t3"): None,
            ("public", "t4"): None,
        }
        bundle = _bundle(tables, descriptions, [])

        report = build_quality_report(bundle, ".sonar/")

        assert report.description_coverage == 0.6
        assert report.description_present == 3
        assert report.description_null == 2

    def test_orphan_tables_listed(self) -> None:
        tables = [_table("public", "a"), _table("public", "b"), _table("public", "lonely")]
        descriptions = {
            ("public", "a"): _description("public", "a"),
            ("public", "b"): _description("public", "b"),
            ("public", "lonely"): _description("public", "lonely"),
        }
        relationships = [_rel(("public", "a", "id"), ("public", "b", "id"))]
        bundle = _bundle(tables, descriptions, relationships)

        report = build_quality_report(bundle, ".sonar/")

        assert ("public", "lonely") in report.orphan_tables
        assert ("public", "a") not in report.orphan_tables

    def test_disconnected_components(self) -> None:
        tables = [_table("public", n) for n in ("a", "b", "c", "d", "e")]
        descriptions = {(t.schema, t.name): _description(t.schema, t.name) for t in tables}
        relationships = [
            _rel(("public", "a", "id"), ("public", "b", "id")),
            _rel(("public", "b", "id"), ("public", "c", "id")),
            _rel(("public", "d", "id"), ("public", "e", "id")),
        ]
        bundle = _bundle(tables, descriptions, relationships)

        report = build_quality_report(bundle, ".sonar/")

        assert report.graph.components == 2
        assert report.graph.largest_component_size == 3
        assert abs(report.graph.largest_component_fraction - 0.6) < 1e-9
        # Mean reachable: 3 nodes reach 3 each (sum 9), 2 nodes reach 2 each (sum 4) -> 13/5 = 2.6
        assert abs(report.graph.mean_reachable - 13 / 5) < 1e-9

    def test_empty_bundle(self) -> None:
        bundle = _bundle([], {}, [])
        report = build_quality_report(bundle, ".sonar/")
        assert report.table_count == 0
        assert report.description_coverage == 0.0
        assert report.relationship_coverage == 0.0
        assert report.graph.components == 0
        assert report.graph.largest_component_size == 0
        assert report.graph.mean_reachable == 0.0

    def test_pii_distribution_counts_columns(self) -> None:
        tables = [
            _table(
                "public",
                "users",
                columns=(
                    Column("id", "int", False, True),
                    Column("email", "text", False, False),
                ),
            ),
        ]
        cols = (
            ColumnDescription(
                name="id",
                description="pk",
                semantic_type=SemanticType.IDENTIFIER,
                pii_risk=PIIRisk.NONE,
                confidence=0.9,
            ),
            ColumnDescription(
                name="email",
                description="user email",
                semantic_type=SemanticType.DIMENSION,
                pii_risk=PIIRisk.HIGH,
                confidence=0.9,
            ),
        )
        td = TableDescription(
            schema="public",
            name="users",
            description="users",
            grain="one row per user",
            domain_hints=(),
            columns=cols,
            confidence=0.9,
        )
        bundle = _bundle(tables, {("public", "users"): td}, [])

        report = build_quality_report(bundle, ".sonar/")

        assert report.pii_distribution["high"] == 1
        assert report.pii_distribution["none"] == 1
        assert report.pii_distribution["low"] == 0
        assert report.pii_distribution["medium"] == 0

    def test_confidence_summaries(self) -> None:
        tables = [_table("public", "a"), _table("public", "b")]
        descriptions = {
            ("public", "a"): _description("public", "a", confidence=0.4),
            ("public", "b"): _description("public", "b", confidence=0.8),
        }
        bundle = _bundle(tables, descriptions, [])

        report = build_quality_report(bundle, ".sonar/")

        assert report.table_confidence is not None
        assert abs(report.table_confidence.mean - 0.6) < 1e-9
        assert report.table_confidence.minimum == 0.4
        assert report.table_confidence.maximum == 0.8
        assert report.table_confidence.count == 2

    def test_confidence_summary_skips_null_descriptions(self) -> None:
        tables = [_table("public", "a"), _table("public", "b")]
        descriptions = {
            ("public", "a"): _description("public", "a", confidence=0.5),
            ("public", "b"): None,
        }
        bundle = _bundle(tables, descriptions, [])

        report = build_quality_report(bundle, ".sonar/")

        assert report.table_confidence is not None
        assert report.table_confidence.count == 1

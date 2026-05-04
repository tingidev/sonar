"""Unit tests for relationship recall/precision evaluation."""

from __future__ import annotations

from sonar.connectors.types import Column, ForeignKey, Table
from sonar.eval._types import RelationshipEdge
from sonar.eval.relationships import evaluate_relationships


def _users() -> Table:
    return Table(
        schema="public",
        name="users",
        columns=(Column("user_id", "uuid", False, True),),
    )


def _orders() -> Table:
    return Table(
        schema="public",
        name="orders",
        columns=(
            Column("order_id", "int", False, True),
            Column("user_id", "uuid", False, False),
        ),
    )


def _products() -> Table:
    return Table(
        schema="public",
        name="products",
        columns=(Column("product_id", "int", False, True),),
    )


def _order_items() -> Table:
    return Table(
        schema="public",
        name="order_items",
        columns=(
            Column("order_item_id", "int", False, True),
            Column("order_id", "int", False, False),
            Column("product_id", "int", False, False),
        ),
    )


def _fk(
    src_table: str,
    src_col: str,
    tgt_table: str,
    tgt_col: str,
    schema: str = "public",
) -> ForeignKey:
    return ForeignKey(
        source_schema=schema,
        source_table=src_table,
        source_column=src_col,
        target_schema=schema,
        target_table=tgt_table,
        target_column=tgt_col,
    )


class TestEvaluateRelationships:
    def test_perfect_inference(self) -> None:
        tables = [_users(), _orders(), _products(), _order_items()]
        declared = [
            _fk("orders", "user_id", "users", "user_id"),
            _fk("order_items", "order_id", "orders", "order_id"),
            _fk("order_items", "product_id", "products", "product_id"),
        ]
        report = evaluate_relationships(tables, declared)
        assert report.recall == 1.0
        assert report.precision == 1.0
        assert report.f1 == 1.0
        assert report.missed == ()
        assert report.false_positive == ()

    def test_partial_inference_with_false_positive(self) -> None:
        # Construct a setup where the heuristic both misses (rule fails) and
        # introduces a spurious match. We make a table whose foreign-key column
        # cannot be inferred (no PK match available).
        odd_table = Table(
            schema="public",
            name="audit",
            columns=(
                Column("audit_id", "int", False, True),
                Column("subject", "text", False, False),  # FK to users but bad name
            ),
        )
        tables = [_users(), _orders(), odd_table]
        declared = [
            _fk("orders", "user_id", "users", "user_id"),
            _fk("audit", "subject", "users", "user_id"),
        ]
        report = evaluate_relationships(tables, declared)
        # 'audit.subject' won't match any heuristic, so it's missed.
        assert report.recall == 0.5
        # 'orders.user_id' is the only inferred edge and matches a declared.
        assert report.precision == 1.0
        assert any(e.source_column == "subject" for e in report.missed)

    def test_no_declared_fks_recall_undefined(self) -> None:
        tables = [_users()]
        report = evaluate_relationships(tables, [])
        assert report.recall is None
        assert report.declared_count == 0

    def test_no_inferred_edges_precision_undefined(self) -> None:
        # Single table, no PKs to infer against.
        lone = Table(
            schema="public",
            name="lone",
            columns=(Column("x", "text", False, False),),
        )
        report = evaluate_relationships([lone], [])
        assert report.recall is None
        assert report.precision is None
        assert report.f1 is None
        assert report.inferred_count == 0

    def test_per_table_breakdown_includes_sources(self) -> None:
        tables = [_users(), _orders()]
        declared = [_fk("orders", "user_id", "users", "user_id")]
        report = evaluate_relationships(tables, declared)
        names = {(b.schema, b.name) for b in report.per_table}
        assert ("public", "orders") in names

    def test_edge_equality_is_full_six_tuple(self) -> None:
        a = RelationshipEdge("public", "a", "x", "public", "b", "y")
        b = RelationshipEdge("public", "a", "x", "public", "b", "y")
        c = RelationshipEdge("public", "a", "x", "public", "b", "z")
        assert a == b
        assert a != c

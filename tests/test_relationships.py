"""Unit tests for sonar.relationships.map_relationships."""

import dataclasses
import json
import logging

import pytest

from sonar.connectors.postgres import Column, ForeignKey, Table
from sonar.relationships import Relationship, RelationshipKind, map_relationships


def _table(schema: str, name: str, cols_spec: list[tuple[str, str, bool, bool]]) -> Table:
    columns = tuple(
        Column(name=c_name, data_type=c_type, nullable=c_nullable, is_primary_key=c_pk)
        for c_name, c_type, c_nullable, c_pk in cols_spec
    )
    return Table(schema=schema, name=name, columns=columns)


def _fk(
    src_schema: str,
    src_table: str,
    src_col: str,
    tgt_schema: str,
    tgt_table: str,
    tgt_col: str,
) -> ForeignKey:
    return ForeignKey(
        source_schema=src_schema,
        source_table=src_table,
        source_column=src_col,
        target_schema=tgt_schema,
        target_table=tgt_table,
        target_column=tgt_col,
    )


def test_relationship_is_frozen():
    rel = Relationship(
        source_schema="public",
        source_table="orders",
        source_column="user_id",
        target_schema="public",
        target_table="users",
        target_column="id",
        kind=RelationshipKind.DECLARED,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        rel.source_schema = "other"  # type: ignore[misc]


def test_relationship_kind_json_round_trip():
    declared_payload = json.dumps(RelationshipKind.DECLARED)
    inferred_payload = json.dumps(RelationshipKind.INFERRED)
    assert json.loads(declared_payload) == "declared"
    assert json.loads(inferred_payload) == "inferred"
    assert RelationshipKind(json.loads(declared_payload)) is RelationshipKind.DECLARED
    assert RelationshipKind(json.loads(inferred_payload)) is RelationshipKind.INFERRED


def test_simple_declared_fk_passes_through():
    tables = [
        _table(
            "public",
            "orders",
            [("id", "integer", False, True), ("user_id", "integer", False, False)],
        ),
        _table("public", "users", [("user_id", "integer", False, True)]),
    ]
    fks = [_fk("public", "orders", "user_id", "public", "users", "user_id")]

    result = map_relationships(tables, fks)

    assert len(result) == 1
    rel = result[0]
    assert rel.kind is RelationshipKind.DECLARED
    assert rel.source_schema == "public"
    assert rel.source_table == "orders"
    assert rel.source_column == "user_id"
    assert rel.target_schema == "public"
    assert rel.target_table == "users"
    assert rel.target_column == "user_id"


def test_composite_declared_fk_produces_aligned_entries():
    tables = [
        _table(
            "public",
            "line_items",
            [
                ("order_id", "integer", False, True),
                ("product_id", "integer", False, True),
                ("a", "integer", False, False),
                ("b", "integer", False, False),
            ],
        ),
        _table(
            "public",
            "parent",
            [("a", "integer", False, True), ("b", "integer", False, True)],
        ),
    ]
    fks = [
        _fk("public", "line_items", "a", "public", "parent", "a"),
        _fk("public", "line_items", "b", "public", "parent", "b"),
    ]

    result = map_relationships(tables, fks)

    assert len(result) == 2
    assert all(r.kind is RelationshipKind.DECLARED for r in result)
    assert result[0].source_column == "a"
    assert result[0].target_column == "a"
    assert result[1].source_column == "b"
    assert result[1].target_column == "b"


def test_cross_schema_declared_fk_preserved_when_target_absent():
    tables = [
        _table(
            "public",
            "events",
            [("id", "integer", False, True), ("tenant_id", "integer", False, False)],
        ),
    ]
    fks = [_fk("public", "events", "tenant_id", "admin", "tenants", "id")]

    result = map_relationships(tables, fks)

    assert len(result) == 1
    rel = result[0]
    assert rel.kind is RelationshipKind.DECLARED
    assert rel.target_schema == "admin"
    assert rel.target_table == "tenants"
    assert rel.target_column == "id"


def test_plural_form_inference():
    tables = [
        _table(
            "public",
            "orders",
            [("id", "integer", False, True), ("user_id", "integer", False, False)],
        ),
        _table("public", "users", [("id", "integer", False, True)]),
    ]

    result = map_relationships(tables, [])

    assert len(result) == 1
    rel = result[0]
    assert rel.kind is RelationshipKind.INFERRED
    assert rel.source_table == "orders"
    assert rel.source_column == "user_id"
    assert rel.target_table == "users"
    assert rel.target_column == "id"


def test_singular_form_inference():
    tables = [
        _table(
            "public",
            "orders",
            [("id", "integer", False, True), ("user_id", "integer", False, False)],
        ),
        _table("public", "user", [("user_id", "integer", False, True)]),
    ]

    result = map_relationships(tables, [])

    assert len(result) == 1
    rel = result[0]
    assert rel.kind is RelationshipKind.INFERRED
    assert rel.target_table == "user"
    assert rel.target_column == "user_id"


def test_ambiguity_emits_no_inferred_edge():
    tables = [
        _table(
            "public",
            "orders",
            [("id", "integer", False, True), ("user_id", "integer", False, False)],
        ),
        _table("public", "user", [("id", "integer", False, True)]),
        _table("public", "users", [("id", "integer", False, True)]),
    ]

    result = map_relationships(tables, [])

    assert result == []


def test_unacceptable_pk_emits_no_edge():
    tables_wrong_name = [
        _table(
            "public",
            "orders",
            [("id", "integer", False, True), ("user_id", "integer", False, False)],
        ),
        _table("public", "users", [("uuid", "integer", False, True)]),
    ]
    assert map_relationships(tables_wrong_name, []) == []

    tables_composite_pk = [
        _table(
            "public",
            "orders",
            [("id", "integer", False, True), ("user_id", "integer", False, False)],
        ),
        _table(
            "public",
            "users",
            [("id", "integer", False, True), ("tenant_id", "integer", False, True)],
        ),
    ]
    assert map_relationships(tables_composite_pk, []) == []

    tables_no_pk = [
        _table(
            "public",
            "orders",
            [("id", "integer", False, True), ("user_id", "integer", False, False)],
        ),
        _table("public", "users", [("id", "integer", False, False)]),
    ]
    assert map_relationships(tables_no_pk, []) == []


def test_cross_schema_candidate_emits_no_edge():
    tables = [
        _table(
            "analytics",
            "events",
            [("id", "integer", False, True), ("user_id", "integer", False, False)],
        ),
        _table("public", "users", [("id", "integer", False, True)]),
    ]

    result = map_relationships(tables, [])

    assert result == []


def test_declared_blocks_inference_on_same_source():
    tables = [
        _table(
            "public",
            "orders",
            [("id", "integer", False, True), ("user_id", "integer", False, False)],
        ),
        _table("public", "users", [("id", "integer", False, True)]),
    ]
    fks = [_fk("public", "orders", "user_id", "public", "users", "id")]

    result = map_relationships(tables, fks)

    assert len(result) == 1
    rel = result[0]
    assert rel.kind is RelationshipKind.DECLARED
    assert rel.source_column == "user_id"
    inferred_with_same_source = [
        r
        for r in result
        if r.kind is RelationshipKind.INFERRED
        and (r.source_schema, r.source_table, r.source_column)
        == ("public", "orders", "user_id")
    ]
    assert inferred_with_same_source == []


def test_deterministic_ordering():
    tables = [
        _table(
            "public",
            "orders",
            [
                ("id", "integer", False, True),
                ("user_id", "integer", False, False),
                ("product_id", "integer", False, False),
            ],
        ),
        _table(
            "public",
            "invoices",
            [
                ("id", "integer", False, True),
                ("customer_id", "integer", False, False),
            ],
        ),
        _table("public", "users", [("id", "integer", False, True)]),
        _table("public", "products", [("id", "integer", False, True)]),
        _table("public", "customers", [("id", "integer", False, True)]),
    ]
    fks = [
        _fk("public", "orders", "product_id", "public", "products", "id"),
        _fk("public", "orders", "user_id", "public", "users", "id"),
    ]

    result = map_relationships(tables, fks)

    declared = [r for r in result if r.kind is RelationshipKind.DECLARED]
    inferred = [r for r in result if r.kind is RelationshipKind.INFERRED]

    assert result == declared + inferred
    assert [r.source_column for r in declared] == ["product_id", "user_id"]
    assert [
        (r.source_schema, r.source_table, r.source_column) for r in inferred
    ] == sorted((r.source_schema, r.source_table, r.source_column) for r in inferred)
    assert [r.source_column for r in inferred] == ["customer_id"]


def test_empty_inputs(caplog):
    caplog.set_level(logging.INFO, logger="sonar.relationships")

    result = map_relationships([], [])

    assert result == []
    records = [r for r in caplog.records if r.name == "sonar.relationships"]
    assert len(records) == 1
    record = records[0]
    assert record.declared == 0
    assert record.inferred == 0
    assert record.tables_scanned == 0


def test_logging_contract(caplog):
    caplog.set_level(logging.INFO, logger="sonar.relationships")

    tables = [
        _table(
            "public",
            "orders",
            [
                ("id", "integer", False, True),
                ("user_id", "integer", False, False),
                ("secret_value", "text", True, False),
            ],
        ),
        _table("public", "users", [("id", "integer", False, True)]),
    ]
    fks = [_fk("public", "orders", "user_id", "public", "users", "id")]

    map_relationships(tables, fks)

    records = [r for r in caplog.records if r.name == "sonar.relationships"]
    assert len(records) == 1
    record = records[0]
    assert record.levelno == logging.INFO
    assert isinstance(record.declared, int)
    assert isinstance(record.inferred, int)
    assert isinstance(record.tables_scanned, int)
    assert record.declared == 1
    assert record.inferred == 0
    assert record.tables_scanned == 2

    haystack = " ".join(
        str(v) for k, v in record.__dict__.items() if isinstance(v, (str, bytes))
    )
    assert "secret_value" not in haystack

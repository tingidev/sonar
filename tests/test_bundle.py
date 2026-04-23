"""Unit tests for ContextBundle / BundleMeta dataclasses and DSN sanitisation."""

from __future__ import annotations

import dataclasses

import pytest

from sonar.connectors.postgres import Column, Table
from sonar.engine.describe import (
    ColumnDescription,
    PIIRisk,
    SemanticType,
    TableDescription,
)
from sonar.index.bundle import (
    SCHEMA_VERSION,
    BundleMeta,
    ContextBundle,
    format_database_label,
)
from sonar.relationships import Relationship, RelationshipKind


def _meta() -> BundleMeta:
    return BundleMeta(
        schema_version=SCHEMA_VERSION,
        generated_at="2026-04-23T12:00:00Z",
        connector="postgres",
        database="sonar@localhost:5433/sonar_test",
    )


def _users_table() -> Table:
    return Table(
        schema="public",
        name="users",
        columns=(
            Column(name="user_id", data_type="uuid", nullable=False, is_primary_key=True),
            Column(name="email", data_type="text", nullable=False, is_primary_key=False),
        ),
    )


def _users_description() -> TableDescription:
    return TableDescription(
        schema="public",
        name="users",
        description="Users table",
        grain="one row per user",
        domain_hints=("identity",),
        columns=(
            ColumnDescription(
                name="user_id",
                description="Primary key",
                semantic_type=SemanticType.IDENTIFIER,
                pii_risk=PIIRisk.NONE,
                confidence=0.9,
            ),
            ColumnDescription(
                name="email",
                description="Email address",
                semantic_type=SemanticType.DIMENSION,
                pii_risk=PIIRisk.HIGH,
                confidence=0.9,
            ),
        ),
        confidence=0.9,
    )


def _orders_table() -> Table:
    return Table(
        schema="public",
        name="orders",
        columns=(
            Column(name="order_id", data_type="uuid", nullable=False, is_primary_key=True),
        ),
    )


class TestBundleMeta:
    def test_is_frozen(self) -> None:
        meta = _meta()
        with pytest.raises(dataclasses.FrozenInstanceError):
            meta.connector = "other"  # type: ignore[misc]

    def test_carries_version_fields(self) -> None:
        meta = _meta()
        assert meta.schema_version == 1
        assert meta.connector == "postgres"
        assert meta.database == "sonar@localhost:5433/sonar_test"


class TestContextBundle:
    def test_is_frozen(self) -> None:
        bundle = ContextBundle(
            meta=_meta(),
            tables=(),
            descriptions={},
            relationships=(),
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            bundle.meta = _meta()  # type: ignore[misc]

    def test_tables_field_is_tuple(self) -> None:
        bundle = ContextBundle(
            meta=_meta(),
            tables=(_users_table(),),
            descriptions={("public", "users"): _users_description()},
            relationships=(),
        )
        assert isinstance(bundle.tables, tuple)

    def test_relationships_field_is_tuple(self) -> None:
        rel = Relationship(
            source_schema="public",
            source_table="orders",
            source_column="user_id",
            target_schema="public",
            target_table="users",
            target_column="user_id",
            kind=RelationshipKind.DECLARED,
        )
        bundle = ContextBundle(
            meta=_meta(),
            tables=(),
            descriptions={},
            relationships=(rel,),
        )
        assert isinstance(bundle.relationships, tuple)

    def test_none_description_preserved_on_equality(self) -> None:
        bundle_a = ContextBundle(
            meta=_meta(),
            tables=(_users_table(), _orders_table()),
            descriptions={
                ("public", "users"): _users_description(),
                ("public", "orders"): None,
            },
            relationships=(),
        )
        bundle_b = ContextBundle(
            meta=_meta(),
            tables=(_users_table(), _orders_table()),
            descriptions={
                ("public", "users"): _users_description(),
                ("public", "orders"): None,
            },
            relationships=(),
        )
        assert bundle_a == bundle_b
        assert bundle_a.descriptions[("public", "orders")] is None
        assert ("public", "orders") in bundle_a.descriptions


class TestFormatDatabaseLabel:
    def test_password_is_stripped(self) -> None:
        label = format_database_label(
            "postgresql://sonar:secret@localhost:5433/sonar_test"
        )
        assert "secret" not in label
        assert label == "sonar@localhost:5433/sonar_test"

    def test_no_password(self) -> None:
        label = format_database_label(
            "postgresql://sonar@localhost:5433/sonar_test"
        )
        assert label == "sonar@localhost:5433/sonar_test"

    def test_bare_hostname_dsn(self) -> None:
        label = format_database_label("postgresql://localhost/mydb")
        assert label == "localhost/mydb"

    def test_unparseable_falls_back_to_unknown(self) -> None:
        assert format_database_label("") == "unknown"
        assert format_database_label("not a dsn at all") == "unknown"
        assert format_database_label("localhost") == "unknown"

    def test_password_never_appears_even_for_odd_input(self) -> None:
        # Pathological: password contains delimiters. Either the label strips the
        # password and keeps the host, or it falls back to the safe placeholder.
        # Either is acceptable; leaking the password is not.
        label = format_database_label(
            "postgresql://u:p@ss:wor/d@localhost:5433/db"
        )
        assert "p@ss:wor/d" not in label
        assert "secret" not in label

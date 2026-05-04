"""Unit tests for the ContextStore JSON persistence layer."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from sonar.connectors.types import Column, Table
from sonar.engine.describe import (
    ColumnDescription,
    PIIRisk,
    SemanticType,
    TableDescription,
)
from sonar.index.bundle import (
    SCHEMA_VERSION,
    BundleIntegrityError,
    BundleMeta,
    BundleVersionError,
    ContextBundle,
)
from sonar.index.store import ContextStore
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


def _orders_table() -> Table:
    return Table(
        schema="public",
        name="orders",
        columns=(
            Column(name="order_id", data_type="uuid", nullable=False, is_primary_key=True),
            Column(name="user_id", data_type="uuid", nullable=False, is_primary_key=False),
        ),
    )


def _users_description() -> TableDescription:
    return TableDescription(
        schema="public",
        name="users",
        description="Application users",
        grain="one row per user",
        domain_hints=("identity",),
        columns=(
            ColumnDescription(
                name="user_id",
                description="Primary key",
                semantic_type=SemanticType.IDENTIFIER,
                pii_risk=PIIRisk.NONE,
                confidence=0.95,
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


def _orders_rel() -> Relationship:
    return Relationship(
        source_schema="public",
        source_table="orders",
        source_column="user_id",
        target_schema="public",
        target_table="users",
        target_column="user_id",
        kind=RelationshipKind.DECLARED,
    )


def _bundle_with_null_description() -> ContextBundle:
    return ContextBundle(
        meta=_meta(),
        tables=(_users_table(), _orders_table()),
        descriptions={
            ("public", "users"): _users_description(),
            ("public", "orders"): None,
        },
        relationships=(_orders_rel(),),
    )


class TestRoundTrip:
    def test_populated_and_null_descriptions_round_trip(self, tmp_path: Path) -> None:
        store = ContextStore(tmp_path / "bundle")
        bundle = _bundle_with_null_description()

        store.write(bundle)
        read_back = store.read()

        assert read_back == bundle

    def test_descriptions_value_is_none_on_disk_and_in_memory(self, tmp_path: Path) -> None:
        store = ContextStore(tmp_path / "bundle")
        store.write(_bundle_with_null_description())

        raw = json.loads((tmp_path / "bundle" / "descriptions.json").read_text())
        assert raw["public.orders"] is None
        assert raw["public.users"] is not None

        read_back = store.read()
        assert read_back is not None
        assert read_back.descriptions[("public", "orders")] is None

    def test_write_creates_missing_directory(self, tmp_path: Path) -> None:
        target = tmp_path / "nested" / "bundle"
        assert not target.exists()

        store = ContextStore(target)
        store.write(_bundle_with_null_description())

        assert target.exists()
        for fname in ("meta.json", "tables.json", "descriptions.json", "relationships.json"):
            assert (target / fname).exists()

    def test_second_write_overwrites_first(self, tmp_path: Path) -> None:
        store = ContextStore(tmp_path / "bundle")
        bundle_a = ContextBundle(
            meta=_meta(),
            tables=(_users_table(),),
            descriptions={("public", "users"): _users_description()},
            relationships=(),
        )
        bundle_b = _bundle_with_null_description()

        store.write(bundle_a)
        store.write(bundle_b)

        assert store.read() == bundle_b


class TestMissingBundle:
    def test_missing_directory_returns_none(self, tmp_path: Path) -> None:
        store = ContextStore(tmp_path / "does-not-exist")
        assert store.read() is None

    def test_empty_directory_returns_none(self, tmp_path: Path) -> None:
        target = tmp_path / "bundle"
        target.mkdir()
        store = ContextStore(target)
        assert store.read() is None


class TestVersion:
    def test_mismatched_version_raises(self, tmp_path: Path) -> None:
        target = tmp_path / "bundle"
        target.mkdir()

        (target / "meta.json").write_text(
            json.dumps(
                {
                    "schema_version": 999,
                    "generated_at": "2026-04-23T12:00:00Z",
                    "connector": "postgres",
                    "database": "sonar@localhost/db",
                }
            )
        )

        store = ContextStore(target)
        with pytest.raises(BundleVersionError) as excinfo:
            store.read()

        assert excinfo.value.expected == SCHEMA_VERSION
        assert excinfo.value.found == 999


class TestIntegrity:
    def test_orphan_description_key_raises(self, tmp_path: Path) -> None:
        store = ContextStore(tmp_path / "bundle")
        store.write(_bundle_with_null_description())

        desc_path = tmp_path / "bundle" / "descriptions.json"
        desc = json.loads(desc_path.read_text())
        desc["public.ghost"] = None
        desc_path.write_text(json.dumps(desc))

        with pytest.raises(BundleIntegrityError) as excinfo:
            store.read()

        assert "ghost" in str(excinfo.value)

    def test_missing_description_key_raises(self, tmp_path: Path) -> None:
        store = ContextStore(tmp_path / "bundle")
        store.write(_bundle_with_null_description())

        desc_path = tmp_path / "bundle" / "descriptions.json"
        desc = json.loads(desc_path.read_text())
        del desc["public.orders"]
        desc_path.write_text(json.dumps(desc))

        with pytest.raises(BundleIntegrityError) as excinfo:
            store.read()

        assert "orders" in str(excinfo.value)

    def test_malformed_description_key_raises(self, tmp_path: Path) -> None:
        store = ContextStore(tmp_path / "bundle")
        store.write(_bundle_with_null_description())

        desc_path = tmp_path / "bundle" / "descriptions.json"
        desc_path.write_text(json.dumps({"nodot": None}))

        with pytest.raises(BundleIntegrityError):
            store.read()

    def test_dotted_table_name_in_key_raises(self, tmp_path: Path) -> None:
        store = ContextStore(tmp_path / "bundle")
        store.write(_bundle_with_null_description())

        desc_path = tmp_path / "bundle" / "descriptions.json"
        desc = json.loads(desc_path.read_text())
        desc["public.schema.weird"] = None
        desc_path.write_text(json.dumps(desc))

        with pytest.raises(BundleIntegrityError, match="dotted table name"):
            store.read()


class TestLogging:
    def test_write_emits_one_info_record_with_counts(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        store = ContextStore(tmp_path / "bundle")
        bundle = _bundle_with_null_description()

        caplog.clear()
        with caplog.at_level(logging.INFO, logger="sonar.index"):
            store.write(bundle)

        records = [r for r in caplog.records if r.name == "sonar.index"]
        assert len(records) == 1
        rec = records[0]
        assert rec.tables == 2
        assert rec.descriptions_present == 1
        assert rec.descriptions_null == 1
        assert rec.relationships == 1

    def test_read_emits_one_info_record_with_counts(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        store = ContextStore(tmp_path / "bundle")
        bundle = _bundle_with_null_description()
        store.write(bundle)

        caplog.clear()
        with caplog.at_level(logging.INFO, logger="sonar.index"):
            store.read()

        records = [r for r in caplog.records if r.name == "sonar.index"]
        assert len(records) == 1
        rec = records[0]
        assert rec.tables == 2
        assert rec.descriptions_present == 1
        assert rec.descriptions_null == 1
        assert rec.relationships == 1

    def test_log_records_do_not_include_description_content(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        store = ContextStore(tmp_path / "bundle")
        bundle = _bundle_with_null_description()

        caplog.clear()
        with caplog.at_level(logging.INFO, logger="sonar.index"):
            store.write(bundle)

        for rec in caplog.records:
            if rec.name != "sonar.index":
                continue
            for value in rec.__dict__.values():
                if isinstance(value, str):
                    assert "Application users" not in value
                    assert "Primary key" not in value
                    assert "sonar@localhost" not in value

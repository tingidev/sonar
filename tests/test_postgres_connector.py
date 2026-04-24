import datetime
import decimal
import json
import uuid

import pytest

from sonar.connectors.postgres import (
    PostgresConnector,
    _coerce_value,
    _foreign_keys_from_rows,
    _serialize_row,
    _tables_from_rows,
)


class TestValueCoercion:
    def test_uuid_becomes_string(self):
        u = uuid.UUID("00000000-0000-0000-0000-000000000001")
        assert _coerce_value(u) == str(u)

    def test_datetime_becomes_iso_string(self):
        dt = datetime.datetime(2026, 4, 21, 12, 30, 0)
        assert _coerce_value(dt) == dt.isoformat()

    def test_date_becomes_iso_string(self):
        d = datetime.date(2026, 4, 21)
        assert _coerce_value(d) == "2026-04-21"

    def test_decimal_becomes_float(self):
        assert _coerce_value(decimal.Decimal("9.99")) == 9.99

    def test_bytes_becomes_sentinel(self):
        assert _coerce_value(b"raw-bytes") == "<binary>"

    def test_scalar_passes_through(self):
        assert _coerce_value(42) == 42
        assert _coerce_value("text") == "text"
        assert _coerce_value(None) is None

    def test_serialize_row_does_not_mutate_input(self):
        original = {"id": uuid.UUID("00000000-0000-0000-0000-000000000001"), "n": 1}
        copy = dict(original)
        result = _serialize_row(original)
        assert original == copy
        assert result is not original
        assert result["id"] == str(original["id"])


def _table_row(
    schema: str = "public",
    table_name: str = "users",
    column_name: str = "id",
) -> dict:
    return {
        "schema": schema,
        "table_name": table_name,
        "column_name": column_name,
        "data_type": "integer",
        "udt_name": "int4",
        "is_nullable": "NO",
        "is_primary_key": True,
        "column_default": None,
    }


def _fk_row(
    source_schema: str = "public",
    source_table: str = "orders",
    target_schema: str = "public",
    target_table: str = "users",
) -> dict:
    return {
        "source_schema": source_schema,
        "source_table": source_table,
        "source_column": "user_id",
        "target_schema": target_schema,
        "target_table": target_table,
        "target_column": "user_id",
    }


class TestDottedIdentifierRejection:
    def test_dotted_schema_in_tables_raises(self):
        rows = [_table_row(schema="weird.schema", table_name="users")]
        with pytest.raises(ValueError, match=r"weird\.schema"):
            _tables_from_rows(rows)

    def test_dotted_table_in_tables_raises(self):
        rows = [_table_row(schema="public", table_name="weird.table")]
        with pytest.raises(ValueError, match=r"weird\.table"):
            _tables_from_rows(rows)

    def test_undotted_tables_parse(self):
        rows = [_table_row()]
        tables = _tables_from_rows(rows)
        assert len(tables) == 1
        assert tables[0].schema == "public"
        assert tables[0].name == "users"

    def test_dotted_source_schema_in_fks_raises(self):
        rows = [_fk_row(source_schema="a.b")]
        with pytest.raises(ValueError, match=r"a\.b"):
            _foreign_keys_from_rows(rows)

    def test_dotted_source_table_in_fks_raises(self):
        rows = [_fk_row(source_table="a.b")]
        with pytest.raises(ValueError, match=r"a\.b"):
            _foreign_keys_from_rows(rows)

    def test_dotted_target_schema_in_fks_raises(self):
        rows = [_fk_row(target_schema="a.b")]
        with pytest.raises(ValueError, match=r"a\.b"):
            _foreign_keys_from_rows(rows)

    def test_dotted_target_table_in_fks_raises(self):
        rows = [_fk_row(target_table="a.b")]
        with pytest.raises(ValueError, match=r"a\.b"):
            _foreign_keys_from_rows(rows)

    def test_undotted_fks_parse(self):
        rows = [_fk_row()]
        fks = _foreign_keys_from_rows(rows)
        assert len(fks) == 1


class TestConnectionLifecycle:
    async def test_methods_outside_context_raise(self):
        connector = PostgresConnector("postgresql://unused")
        expected = "PostgresConnector must be used as an async context manager"

        with pytest.raises(RuntimeError, match=expected):
            await connector.discover_tables()
        with pytest.raises(RuntimeError, match=expected):
            await connector.discover_relationships()
        with pytest.raises(RuntimeError, match=expected):
            await connector.sample_table("public", "users")


@pytest.mark.integration
class TestSchemaIntrospection:
    async def test_discover_tables_returns_all_user_tables(self, connector):
        tables = await connector.discover_tables()
        assert len(tables) == 7
        assert all(t.schema == "public" for t in tables)

    async def test_default_schemas_exclude_system(self, connector):
        tables = await connector.discover_tables()
        for t in tables:
            assert t.schema != "information_schema"
            assert not t.schema.startswith("pg_")

    async def test_discover_tables_schema_filter(self, connector):
        public = await connector.discover_tables(schemas=["public"])
        assert len(public) == 7

        empty = await connector.discover_tables(schemas=["nonexistent"])
        assert empty == []

    async def test_discover_tables_empty_schemas_short_circuits(self, connector):
        # Passing [] skips the query entirely and returns [] without hitting the DB.
        assert await connector.discover_tables(schemas=[]) == []

    async def test_column_types_correct(self, connector):
        tables = {t.name: t for t in await connector.discover_tables()}

        users_cols = {c.name: c for c in tables["users"].columns}
        assert users_cols["user_id"].data_type == "uuid"

        orders_cols = {c.name: c for c in tables["orders"].columns}
        assert orders_cols["placed_at"].data_type == "timestamp with time zone"

        products_cols = {c.name: c for c in tables["products"].columns}
        assert products_cols["price"].data_type == "numeric"

    async def test_array_type_surfaces_udt_name(self, connector):
        tables = {t.name: t for t in await connector.discover_tables()}
        tag_cols = {c.name: c for c in tables["tags"].columns}
        assert tag_cols["synonyms"].data_type == "_text"

    async def test_user_defined_type_surfaces_udt_name(self, connector):
        tables = {t.name: t for t in await connector.discover_tables()}
        orders_cols = {c.name: c for c in tables["orders"].columns}
        assert orders_cols["status"].data_type == "order_status"

    async def test_composite_pk_detected(self, connector):
        tables = {t.name: t for t in await connector.discover_tables()}

        order_items_cols = {c.name: c for c in tables["order_items"].columns}
        assert order_items_cols["order_id"].is_primary_key is True
        assert order_items_cols["product_id"].is_primary_key is True
        assert order_items_cols["quantity"].is_primary_key is False
        assert order_items_cols["unit_price"].is_primary_key is False

        product_tags_cols = {c.name: c for c in tables["product_tags"].columns}
        assert product_tags_cols["product_id"].is_primary_key is True
        assert product_tags_cols["tag_id"].is_primary_key is True


@pytest.mark.integration
class TestForeignKeyExtraction:
    async def test_discover_relationships_finds_all_fks(self, connector):
        fks = await connector.discover_relationships()
        pairs = {
            (fk.source_table, fk.source_column, fk.target_table, fk.target_column) for fk in fks
        }

        expected = {
            ("addresses", "user_id", "users", "user_id"),
            ("orders", "user_id", "users", "user_id"),
            ("order_items", "order_id", "orders", "order_id"),
            ("order_items", "product_id", "products", "product_id"),
            ("product_tags", "product_id", "products", "product_id"),
            ("product_tags", "tag_id", "tags", "tag_id"),
        }
        assert expected.issubset(pairs)

    async def test_simple_fk_fields_correct(self, connector):
        fks = await connector.discover_relationships()
        orders_user_fk = next(
            fk for fk in fks if fk.source_table == "orders" and fk.source_column == "user_id"
        )
        assert orders_user_fk.source_schema == "public"
        assert orders_user_fk.source_table == "orders"
        assert orders_user_fk.source_column == "user_id"
        assert orders_user_fk.target_schema == "public"
        assert orders_user_fk.target_table == "users"
        assert orders_user_fk.target_column == "user_id"


@pytest.mark.integration
class TestRowSampling:
    async def test_sample_table_default_limit(self, connector):
        rows = await connector.sample_table("public", "products")
        assert len(rows) == 5

    async def test_sample_table_custom_limit(self, connector):
        rows = await connector.sample_table("public", "products", limit=3)
        assert len(rows) == 3

    async def test_sample_table_row_shape(self, connector):
        rows = await connector.sample_table("public", "users")
        for row in rows:
            assert isinstance(row, dict)
            assert set(row.keys()) == {"user_id", "email", "name", "created_at"}

    async def test_sample_table_values_json_serialisable(self, connector):
        users = await connector.sample_table("public", "users")
        for row in users:
            assert isinstance(row["user_id"], str)
            assert isinstance(row["created_at"], str)
            # ISO 8601 sanity: contains T or space, and a digit-year prefix
            assert row["created_at"][:4].isdigit()
            json.dumps(row)

        products = await connector.sample_table("public", "products")
        for row in products:
            assert isinstance(row["price"], float)
            json.dumps(row)

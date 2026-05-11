"""Tests for the DuckDB connector — real in-memory/file DuckDB, no mocks or fakes."""

from __future__ import annotations

import importlib.util

import duckdb
import pytest

from sonar.connectors.duckdb import (
    DuckDBConnector,
    _foreign_keys_from_rows,
    _quote_identifier,
    _tables_from_rows,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_file(tmp_path: pytest.TempPathFactory) -> str:
    """Temp DuckDB file with `users` and `orders` tables and a FK."""
    path = str(tmp_path / "test.duckdb")
    conn = duckdb.connect(path)
    conn.execute(
        "CREATE TABLE users (id INTEGER PRIMARY KEY, name VARCHAR, created_at TIMESTAMP)"
    )
    conn.execute(
        "CREATE TABLE orders ("
        "id INTEGER PRIMARY KEY, "
        "user_id INTEGER REFERENCES users(id), "
        "amount DECIMAL(10, 2), "
        "placed_at TIMESTAMP)"
    )
    conn.execute("INSERT INTO users VALUES (1, 'Alice', '2025-01-01')")
    conn.execute("INSERT INTO users VALUES (2, 'Bob', '2025-02-01')")
    conn.execute("INSERT INTO orders VALUES (100, 1, 99.99, '2025-03-01')")
    conn.close()
    return path


# ---------------------------------------------------------------------------
# 5.2 — discover_tables: shape, columns, types, nullability, PK flags
# ---------------------------------------------------------------------------


class TestDiscoverTables:
    async def test_returns_both_tables(self, db_file: str) -> None:
        async with DuckDBConnector(db_file) as c:
            tables = await c.discover_tables()
        names = {(t.schema, t.name) for t in tables}
        assert names == {("main", "users"), ("main", "orders")}

    async def test_users_columns_present(self, db_file: str) -> None:
        async with DuckDBConnector(db_file) as c:
            tables = await c.discover_tables()
        users = next(t for t in tables if t.name == "users")
        col_names = {col.name for col in users.columns}
        assert col_names == {"id", "name", "created_at"}

    async def test_primary_key_flagged(self, db_file: str) -> None:
        async with DuckDBConnector(db_file) as c:
            tables = await c.discover_tables()
        users = next(t for t in tables if t.name == "users")
        id_col = next(col for col in users.columns if col.name == "id")
        assert id_col.is_primary_key

    async def test_nullable_column_flagged(self, db_file: str) -> None:
        async with DuckDBConnector(db_file) as c:
            tables = await c.discover_tables()
        users = next(t for t in tables if t.name == "users")
        name_col = next(col for col in users.columns if col.name == "name")
        assert name_col.nullable

    async def test_primary_key_not_nullable(self, db_file: str) -> None:
        async with DuckDBConnector(db_file) as c:
            tables = await c.discover_tables()
        users = next(t for t in tables if t.name == "users")
        id_col = next(col for col in users.columns if col.name == "id")
        assert not id_col.nullable


# ---------------------------------------------------------------------------
# 5.3 — discover_tables: row_count from duckdb_tables()
# ---------------------------------------------------------------------------


class TestRowCounts:
    async def test_row_count_populated(self, db_file: str) -> None:
        async with DuckDBConnector(db_file) as c:
            tables = await c.discover_tables()
        users = next(t for t in tables if t.name == "users")
        assert users.row_count is not None

    async def test_row_count_is_non_negative(self, db_file: str) -> None:
        async with DuckDBConnector(db_file) as c:
            tables = await c.discover_tables()
        for t in tables:
            assert t.row_count is None or t.row_count >= 0


# ---------------------------------------------------------------------------
# 5.4 — discover_relationships: FK orders.user_id → users.id
# ---------------------------------------------------------------------------


class TestDiscoverRelationships:
    async def test_fk_orders_to_users(self, db_file: str) -> None:
        async with DuckDBConnector(db_file) as c:
            fks = await c.discover_relationships()
        assert len(fks) == 1
        fk = fks[0]
        assert fk.source_schema == "main"
        assert fk.source_table == "orders"
        assert fk.source_column == "user_id"
        assert fk.target_schema == "main"
        assert fk.target_table == "users"
        assert fk.target_column == "id"


# ---------------------------------------------------------------------------
# 5.5 — sample_table: rows returned and serialized correctly
# ---------------------------------------------------------------------------


class TestSampleTable:
    async def test_sample_returns_rows(self, db_file: str) -> None:
        async with DuckDBConnector(db_file) as c:
            rows = await c.sample_table("main", "orders")
        assert len(rows) == 1
        row = rows[0]
        assert row["id"] == 100
        assert row["user_id"] == 1

    async def test_decimal_coerced_to_float(self, db_file: str) -> None:
        async with DuckDBConnector(db_file) as c:
            rows = await c.sample_table("main", "orders")
        assert isinstance(rows[0]["amount"], float)
        assert rows[0]["amount"] == pytest.approx(99.99)

    async def test_dotted_schema_rejected(self, db_file: str) -> None:
        async with DuckDBConnector(db_file) as c:
            with pytest.raises(ValueError, match=r"identifier contains '\.'"):
                await c.sample_table("main.sub", "orders")

    async def test_dotted_table_rejected(self, db_file: str) -> None:
        async with DuckDBConnector(db_file) as c:
            with pytest.raises(ValueError, match=r"identifier contains '\.'"):
                await c.sample_table("main", "orders.x")


# ---------------------------------------------------------------------------
# 5.6 — schema enumeration: multi-schema discovery and explicit filter
# ---------------------------------------------------------------------------


class TestSchemaEnumeration:
    @pytest.fixture
    def multi_schema_db(self, tmp_path: pytest.TempPathFactory) -> str:
        path = str(tmp_path / "multi.duckdb")
        conn = duckdb.connect(path)
        conn.execute("CREATE TABLE main_t (id INTEGER)")
        conn.execute("CREATE SCHEMA staging")
        conn.execute("CREATE TABLE staging.staging_t (id INTEGER)")
        conn.close()
        return path

    async def test_both_schemas_discovered_without_filter(
        self, multi_schema_db: str
    ) -> None:
        async with DuckDBConnector(multi_schema_db) as c:
            tables = await c.discover_tables()
        schemas_found = {t.schema for t in tables}
        assert "main" in schemas_found
        assert "staging" in schemas_found

    async def test_explicit_filter_respected(self, multi_schema_db: str) -> None:
        async with DuckDBConnector(multi_schema_db) as c:
            tables = await c.discover_tables(schemas=["main"])
        schemas_found = {t.schema for t in tables}
        assert schemas_found == {"main"}

    async def test_system_schemas_excluded(self, multi_schema_db: str) -> None:
        async with DuckDBConnector(multi_schema_db) as c:
            tables = await c.discover_tables()
        schemas_found = {t.schema for t in tables}
        assert "information_schema" not in schemas_found
        assert "pg_catalog" not in schemas_found

    async def test_three_schemas_all_discovered(self, tmp_path: pytest.TempPathFactory) -> None:
        path = str(tmp_path / "three.duckdb")
        conn = duckdb.connect(path)
        conn.execute("CREATE TABLE main_t (id INTEGER)")
        conn.execute("CREATE SCHEMA staging")
        conn.execute("CREATE TABLE staging.staging_t (id INTEGER)")
        conn.execute("CREATE SCHEMA marts")
        conn.execute("CREATE TABLE marts.marts_t (id INTEGER)")
        conn.close()

        async with DuckDBConnector(path) as c:
            tables = await c.discover_tables()
        schemas_found = {t.schema for t in tables}
        assert schemas_found == {"main", "staging", "marts"}


# ---------------------------------------------------------------------------
# 5.7 — :memory: path opens and discovers tables
# ---------------------------------------------------------------------------


class TestInMemoryPath:
    async def test_memory_connector_opens_and_returns_empty(self) -> None:
        async with DuckDBConnector(":memory:") as c:
            tables = await c.discover_tables()
        assert tables == []

    async def test_memory_connector_relationships_returns_empty(self) -> None:
        async with DuckDBConnector(":memory:") as c:
            fks = await c.discover_relationships()
        assert fks == []


# ---------------------------------------------------------------------------
# 5.8 — context manager guard
# ---------------------------------------------------------------------------


class TestContextManagerGuard:
    async def test_discover_tables_outside_context_raises(self, db_file: str) -> None:
        c = DuckDBConnector(db_file)
        with pytest.raises(RuntimeError, match="context manager"):
            await c.discover_tables()

    async def test_discover_relationships_outside_context_raises(self, db_file: str) -> None:
        c = DuckDBConnector(db_file)
        with pytest.raises(RuntimeError, match="context manager"):
            await c.discover_relationships()

    async def test_sample_table_outside_context_raises(self, db_file: str) -> None:
        c = DuckDBConnector(db_file)
        with pytest.raises(RuntimeError, match="context manager"):
            await c.sample_table("main", "users")


# ---------------------------------------------------------------------------
# Pure-unit tests — quote identifier, tables_from_rows, foreign_keys_from_rows
# ---------------------------------------------------------------------------


class TestQuoteIdentifier:
    def test_simple_name(self) -> None:
        assert _quote_identifier("users") == '"users"'

    def test_escapes_internal_double_quote(self) -> None:
        assert _quote_identifier('we"ird') == '"we""ird"'

    def test_rejects_null_byte(self) -> None:
        with pytest.raises(ValueError, match="null byte"):
            _quote_identifier("a\x00b")


class TestTablesFromRows:
    def test_dotted_schema_rejected(self) -> None:
        rows = [
            {
                "schema": "main.sub",
                "table_name": "users",
                "column_name": "id",
                "data_type": "INTEGER",
                "is_nullable": "NO",
                "column_default": None,
                "is_primary_key": True,
                "row_count": 0,
                "ordinal_position": 1,
            }
        ]
        with pytest.raises(ValueError, match=r"identifier contains '\.'"):
            _tables_from_rows(rows)

    def test_dotted_table_rejected(self) -> None:
        rows = [
            {
                "schema": "main",
                "table_name": "users.sub",
                "column_name": "id",
                "data_type": "INTEGER",
                "is_nullable": "NO",
                "column_default": None,
                "is_primary_key": True,
                "row_count": 0,
                "ordinal_position": 1,
            }
        ]
        with pytest.raises(ValueError, match=r"identifier contains '\.'"):
            _tables_from_rows(rows)


class TestForeignKeysFromRows:
    def test_dotted_target_table_rejected(self) -> None:
        rows = [
            {
                "source_schema": "main",
                "source_table": "orders",
                "source_column": "user_id",
                "target_schema": "main",
                "target_table": "users.x",
                "target_column": "id",
            }
        ]
        with pytest.raises(ValueError, match=r"identifier contains '\.'"):
            _foreign_keys_from_rows(rows)


# ---------------------------------------------------------------------------
# 5.9 — optional dep guard
# ---------------------------------------------------------------------------


class TestOptionalDepGuard:
    def test_missing_duckdb_exits_nonzero_with_install_hint(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from sonar.cli import main

        _orig = importlib.util.find_spec
        monkeypatch.setattr(
            "importlib.util.find_spec",
            lambda name: None if name == "duckdb" else _orig(name),
        )
        rc = main(["scan", "duckdb:///path/to/file.duckdb"])
        assert rc == 2
        captured = capsys.readouterr()
        assert "pip install 'sonar[duckdb]'" in captured.err

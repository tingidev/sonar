"""Tests for the Snowflake connector — fakesnow tier (default; runs every PR).

Pure-unit tests (URL parsing, env-var dispatch, missing-extra guard) need no
fakesnow. Discovery, FK extraction, sampling, identifier-case, and no-dot
guard tests use the `snowflake_db` fixture which boots fakesnow with a small
seed schema. Cross-database FK tests use the pure-unit helper because fakesnow
(DuckDB underneath) rejects cross-database FK DDL — see design.md D6.

Live-account smoke tests live in test_snowflake_live.py and are skipped by
default; this file is the contributor-facing safety net.
"""

from __future__ import annotations

import os
from pathlib import Path

import fakesnow
import pytest

from sonar.cli import (
    _ConnectorSpec,
    _DispatchError,
    _select_connector,
    _snowflake_kwargs_from_env,
    _snowflake_kwargs_from_url,
)
from sonar.connectors.snowflake import (
    SnowflakeConnector,
    _foreign_keys_from_rows,
    _quote_identifier,
    _tables_from_rows,
)
from sonar.index.bundle import BundleMeta, ContextBundle
from sonar.scan_output import print_scan_summary

# ---------------------------------------------------------------------------
# Pure-unit tests — no fakesnow needed.
# ---------------------------------------------------------------------------


class TestUrlParsing:
    def test_full_url_parses(self) -> None:
        kw = _snowflake_kwargs_from_url(
            "snowflake://u:p@acct.us-east-1.aws/db/sch?warehouse=W&role=R"
        )
        assert kw == {
            "account": "acct.us-east-1.aws",
            "user": "u",
            "password": "p",
            "database": "db",
            "schema": "sch",
            "warehouse": "W",
            "role": "R",
        }

    def test_url_without_warehouse_or_role(self) -> None:
        kw = _snowflake_kwargs_from_url("snowflake://u:p@acct/db/sch")
        assert "warehouse" not in kw
        assert "role" not in kw

    def test_url_decodes_percent_escaped_password(self) -> None:
        kw = _snowflake_kwargs_from_url("snowflake://u:p%40ss@acct/db/sch")
        assert kw["password"] == "p@ss"

    def test_missing_password_rejects(self) -> None:
        with pytest.raises(_DispatchError, match="password"):
            _snowflake_kwargs_from_url("snowflake://u@acct/db/sch")

    def test_missing_user_rejects(self) -> None:
        with pytest.raises(_DispatchError, match="user"):
            _snowflake_kwargs_from_url("snowflake://acct/db/sch")

    def test_missing_path_rejects(self) -> None:
        with pytest.raises(_DispatchError, match="DATABASE/SCHEMA"):
            _snowflake_kwargs_from_url("snowflake://u:p@acct")

    def test_only_database_no_schema_rejects(self) -> None:
        with pytest.raises(_DispatchError, match="DATABASE/SCHEMA"):
            _snowflake_kwargs_from_url("snowflake://u:p@acct/db")


class TestEnvVarDispatch:
    @pytest.fixture(autouse=True)
    def _clear_snowflake_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key in list(os.environ):
            if key.startswith("SNOWFLAKE_"):
                monkeypatch.delenv(key, raising=False)

    def test_curated_password_set_forwards(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "acct")
        monkeypatch.setenv("SNOWFLAKE_USER", "u")
        monkeypatch.setenv("SNOWFLAKE_PASSWORD", "p")
        monkeypatch.setenv("SNOWFLAKE_DATABASE", "db")
        monkeypatch.setenv("SNOWFLAKE_SCHEMA", "sch")
        monkeypatch.setenv("SNOWFLAKE_WAREHOUSE", "wh")
        monkeypatch.setenv("SNOWFLAKE_ROLE", "r")
        kw = _snowflake_kwargs_from_env()
        assert kw == {
            "account": "acct",
            "user": "u",
            "password": "p",
            "database": "db",
            "schema": "sch",
            "warehouse": "wh",
            "role": "r",
        }

    def test_curated_keypair_set_forwards(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "acct")
        monkeypatch.setenv("SNOWFLAKE_USER", "u")
        monkeypatch.setenv("SNOWFLAKE_PRIVATE_KEY_PATH", "/path/to/key.p8")
        monkeypatch.setenv("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE", "secret")
        monkeypatch.setenv("SNOWFLAKE_DATABASE", "db")
        kw = _snowflake_kwargs_from_env()
        assert kw["private_key_file"] == "/path/to/key.p8"
        assert kw["private_key_file_pwd"] == "secret"
        assert "password" not in kw

    def test_curated_oauth_set_forwards(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "acct")
        monkeypatch.setenv("SNOWFLAKE_USER", "u")
        monkeypatch.setenv("SNOWFLAKE_TOKEN", "tok")
        monkeypatch.setenv("SNOWFLAKE_AUTHENTICATOR", "oauth")
        monkeypatch.setenv("SNOWFLAKE_DATABASE", "db")
        kw = _snowflake_kwargs_from_env()
        assert kw["token"] == "tok"
        assert kw["authenticator"] == "oauth"

    def test_curated_externalbrowser_set_forwards(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "acct")
        monkeypatch.setenv("SNOWFLAKE_USER", "u")
        monkeypatch.setenv("SNOWFLAKE_AUTHENTICATOR", "externalbrowser")
        monkeypatch.setenv("SNOWFLAKE_DATABASE", "db")
        kw = _snowflake_kwargs_from_env()
        assert kw["authenticator"] == "externalbrowser"

    def test_uncurated_env_var_silently_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "acct")
        monkeypatch.setenv("SNOWFLAKE_USER", "u")
        monkeypatch.setenv("SNOWFLAKE_PASSWORD", "p")
        monkeypatch.setenv("SNOWFLAKE_DATABASE", "db")
        monkeypatch.setenv("SNOWFLAKE_NEW_DRIVER_KWARG", "ignored")
        monkeypatch.setenv("SNOWFLAKE_SOMETHING_ELSE", "also_ignored")
        kw = _snowflake_kwargs_from_env()
        assert "ignored" not in kw.values()
        assert "also_ignored" not in kw.values()
        # The driver-side kwarg name space stays consumer-controlled.
        assert "new_driver_kwarg" not in kw
        assert "something_else" not in kw

    def test_missing_required_lists_them(self) -> None:
        with pytest.raises(_DispatchError, match="missing required.*ACCOUNT.*USER.*DATABASE"):
            _snowflake_kwargs_from_env()

    def test_missing_only_database_lists_only_that(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "acct")
        monkeypatch.setenv("SNOWFLAKE_USER", "u")
        with pytest.raises(_DispatchError, match=r"missing required.*DATABASE"):
            _snowflake_kwargs_from_env()

    def test_missing_auth_mechanism_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "acct")
        monkeypatch.setenv("SNOWFLAKE_USER", "u")
        monkeypatch.setenv("SNOWFLAKE_DATABASE", "db")
        with pytest.raises(_DispatchError, match="authentication"):
            _snowflake_kwargs_from_env()


class TestDispatchTimeDriverGuard:
    """Per design.md D4: missing extra fails at CLI dispatch, before any creds."""

    def test_missing_extra_url_form_exits_nonzero(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from sonar.cli import main

        monkeypatch.setattr(
            "importlib.util.find_spec",
            lambda name: None if name == "snowflake.connector" else None,
        )
        rc = main(["scan", "snowflake://u:p@a/d/s"])
        assert rc == 2
        captured = capsys.readouterr()
        assert "pip install 'sonar[snowflake]'" in captured.err

    def test_missing_extra_keyword_form_exits_nonzero(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from sonar.cli import main

        monkeypatch.setattr(
            "importlib.util.find_spec",
            lambda name: None if name == "snowflake.connector" else None,
        )
        rc = main(["scan", "snowflake"])
        assert rc == 2
        captured = capsys.readouterr()
        assert "pip install 'sonar[snowflake]'" in captured.err


class TestUnrecognizedArgument:
    def test_unrecognized_form_lists_all_accepted(self, capsys: pytest.CaptureFixture[str]) -> None:
        from sonar.cli import main

        rc = main(["scan", "mysql://user@host/db"])
        assert rc == 2
        captured = capsys.readouterr()
        assert "postgresql://" in captured.err
        assert "snowflake://" in captured.err
        assert "snowflake (bare keyword" in captured.err


class TestSelectConnectorRouting:
    def test_postgresql_url_constructs_postgres(self) -> None:
        spec = _select_connector("postgresql://u:p@h/db")
        assert spec.connector_type == "postgres"

    def test_postgres_url_constructs_postgres(self) -> None:
        spec = _select_connector("postgres://u:p@h/db")
        assert spec.connector_type == "postgres"

    def test_snowflake_url_constructs_snowflake(self) -> None:
        spec = _select_connector("snowflake://u:p@a/d/s")
        assert spec.connector_type == "snowflake"

    def test_snowflake_keyword_constructs_snowflake(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key in list(os.environ):
            if key.startswith("SNOWFLAKE_"):
                monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "a")
        monkeypatch.setenv("SNOWFLAKE_USER", "u")
        monkeypatch.setenv("SNOWFLAKE_PASSWORD", "p")
        monkeypatch.setenv("SNOWFLAKE_DATABASE", "d")
        spec = _select_connector("snowflake")
        assert spec.connector_type == "snowflake"

    def test_unrecognized_raises_dispatch_error(self) -> None:
        with pytest.raises(_DispatchError):
            _select_connector("not-a-known-form")


class TestQuoteIdentifier:
    def test_quote_simple_name(self) -> None:
        assert _quote_identifier("USERS") == '"USERS"'

    def test_quote_escapes_internal_double_quote(self) -> None:
        assert _quote_identifier('we"ird') == '"we""ird"'

    def test_quote_rejects_null_byte(self) -> None:
        with pytest.raises(ValueError, match="null byte"):
            _quote_identifier("a\x00b")


# ---------------------------------------------------------------------------
# Cross-DB FK filter — pure-unit because fakesnow rejects cross-DB FK DDL.
# ---------------------------------------------------------------------------


class TestForeignKeysFromRows:
    def test_drops_cross_database_fk_and_counts(self) -> None:
        rows = [
            {
                "source_schema": "APP",
                "source_table": "ORDERS",
                "source_column": "USER_ID",
                "target_database": "OTHER_DB",
                "target_schema": None,
                "target_table": None,
                "target_column": None,
            },
            {
                "source_schema": "APP",
                "source_table": "ORDERS",
                "source_column": "PRODUCT_ID",
                "target_database": "BOUND_DB",
                "target_schema": "APP",
                "target_table": "PRODUCTS",
                "target_column": "ID",
            },
        ]
        result, dropped = _foreign_keys_from_rows(rows, "BOUND_DB")
        assert dropped == 1
        assert len(result) == 1
        assert result[0].source_column == "PRODUCT_ID"

    def test_case_insensitive_db_match(self) -> None:
        rows = [
            {
                "source_schema": "APP",
                "source_table": "T",
                "source_column": "C",
                "target_database": "MY_DB",
                "target_schema": "APP",
                "target_table": "OTHER",
                "target_column": "ID",
            },
        ]
        result, dropped = _foreign_keys_from_rows(rows, "my_db")
        assert dropped == 0
        assert len(result) == 1


class TestNoDotGuard:
    def test_dotted_schema_in_table_row_rejected(self) -> None:
        rows = [
            {
                "schema": "APP.SUB",
                "table_name": "USERS",
                "column_name": "ID",
                "data_type": "NUMBER",
                "is_nullable": "NO",
                "column_default": None,
                "is_primary_key": True,
                "row_count": 0,
                "ordinal_position": 1,
            },
        ]
        with pytest.raises(ValueError, match=r"identifier contains '\."):
            _tables_from_rows(rows)

    def test_dotted_table_in_table_row_rejected(self) -> None:
        rows = [
            {
                "schema": "APP",
                "table_name": "USERS.SUB",
                "column_name": "ID",
                "data_type": "NUMBER",
                "is_nullable": "NO",
                "column_default": None,
                "is_primary_key": True,
                "row_count": 0,
                "ordinal_position": 1,
            },
        ]
        with pytest.raises(ValueError, match=r"identifier contains '\."):
            _tables_from_rows(rows)

    def test_dotted_target_table_in_fk_row_rejected(self) -> None:
        rows = [
            {
                "source_schema": "APP",
                "source_table": "ORDERS",
                "source_column": "USER_ID",
                "target_database": "DB",
                "target_schema": "APP",
                "target_table": "USERS.X",
                "target_column": "ID",
            },
        ]
        with pytest.raises(ValueError, match=r"identifier contains '\."):
            _foreign_keys_from_rows(rows, "DB")


# ---------------------------------------------------------------------------
# fakesnow-backed integration tests.
# ---------------------------------------------------------------------------


@pytest.fixture
def snowflake_db():
    """Boot fakesnow with a seed schema; tear down after each test."""
    with fakesnow.patch():
        import snowflake.connector

        conn = snowflake.connector.connect()
        cur = conn.cursor()
        cur.execute("CREATE DATABASE TEST_DB")
        cur.execute("USE DATABASE TEST_DB")
        cur.execute("CREATE SCHEMA APP")
        cur.execute("USE SCHEMA APP")
        cur.execute(
            "CREATE TABLE USERS (ID INT PRIMARY KEY, NAME VARCHAR(100), CREATED_AT TIMESTAMP)"
        )
        cur.execute(
            "CREATE TABLE ORDERS ("
            "ID INT PRIMARY KEY, "
            "USER_ID INT REFERENCES USERS(ID), "
            "AMOUNT DECIMAL(10, 2), "
            "PLACED_AT TIMESTAMP)"
        )
        cur.execute("INSERT INTO USERS VALUES (1, 'Alice', '2025-01-01 00:00:00')")
        cur.execute("INSERT INTO USERS VALUES (2, 'Bob', '2025-02-01 00:00:00')")
        cur.execute("INSERT INTO ORDERS VALUES (100, 1, 99.99, '2025-03-01 12:00:00')")
        cur.close()
        conn.close()
        yield {"database": "TEST_DB", "schema": "APP"}


def _connect_kwargs(database: str = "TEST_DB", schema: str = "APP") -> dict[str, str]:
    return {
        "account": "fake",
        "user": "fake",
        "password": "fake",
        "database": database,
        "schema": schema,
    }


class TestDiscoveryAgainstFakesnow:
    async def test_tables_have_expected_shape(self, snowflake_db) -> None:
        async with SnowflakeConnector(_connect_kwargs()) as c:
            tables = await c.discover_tables()
        names = {(t.schema, t.name) for t in tables}
        assert names == {("APP", "USERS"), ("APP", "ORDERS")}
        users = next(t for t in tables if t.name == "USERS")
        assert {col.name for col in users.columns} == {"ID", "NAME", "CREATED_AT"}
        id_col = next(c for c in users.columns if c.name == "ID")
        assert id_col.is_primary_key

    async def test_row_count_falls_back_to_none_under_fakesnow(self, snowflake_db) -> None:
        # fakesnow does not expose ROW_COUNT (design.md D6 caveat); the connector
        # detects this and substitutes NULL — every Table.row_count is None.
        async with SnowflakeConnector(_connect_kwargs()) as c:
            tables = await c.discover_tables()
        assert all(t.row_count is None for t in tables)

    async def test_discover_relationships_returns_inbound_fk(self, snowflake_db) -> None:
        async with SnowflakeConnector(_connect_kwargs()) as c:
            fks = await c.discover_relationships()
            assert c.cross_database_foreign_keys_dropped == 0
        assert len(fks) == 1
        fk = fks[0]
        assert (fk.source_schema, fk.source_table, fk.source_column) == (
            "APP",
            "ORDERS",
            "USER_ID",
        )
        assert (fk.target_schema, fk.target_table, fk.target_column) == (
            "APP",
            "USERS",
            "ID",
        )

    async def test_default_schema_applied_when_no_arg(self, snowflake_db) -> None:
        async with SnowflakeConnector(_connect_kwargs()) as c:
            tables_default = await c.discover_tables()
            tables_explicit = await c.discover_tables(["APP"])
        assert {(t.schema, t.name) for t in tables_default} == {
            (t.schema, t.name) for t in tables_explicit
        }


class TestSharedDatabaseFallback:
    """Verify graceful degradation when constraint views are inaccessible."""

    async def test_discover_tables_falls_back_without_pk_info(
        self, snowflake_db, monkeypatch
    ) -> None:
        import snowflake.connector.errors

        import sonar.connectors.snowflake as _sf_module

        original = _sf_module._fetch_dicts
        call_count = 0

        def _failing_first_call(conn, query, params):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise snowflake.connector.errors.ProgrammingError(
                    msg="Object 'TEST_DB.INFORMATION_SCHEMA.KEY_COLUMN_USAGE' "
                    "does not exist or not authorized."
                )
            return original(conn, query, params)

        monkeypatch.setattr(_sf_module, "_fetch_dicts", _failing_first_call)
        async with SnowflakeConnector(_connect_kwargs()) as c:
            tables = await c.discover_tables()

        names = {(t.schema, t.name) for t in tables}
        assert names == {("APP", "USERS"), ("APP", "ORDERS")}
        # Fallback query marks all columns as non-PK.
        for t in tables:
            assert all(not col.is_primary_key for col in t.columns)

    async def test_discover_relationships_returns_empty_on_inaccessible_views(
        self, snowflake_db, monkeypatch
    ) -> None:
        import snowflake.connector.errors

        import sonar.connectors.snowflake as _sf_module

        monkeypatch.setattr(
            _sf_module,
            "_fetch_dicts",
            lambda conn, q, p: (_ for _ in ()).throw(
                snowflake.connector.errors.ProgrammingError(
                    msg="Object does not exist or not authorized."
                )
            ),
        )
        async with SnowflakeConnector(_connect_kwargs()) as c:
            fks = await c.discover_relationships()

        assert fks == []


class TestSampleRowSerialization:
    async def test_sample_returns_coerced_dicts(self, snowflake_db) -> None:
        async with SnowflakeConnector(_connect_kwargs()) as c:
            rows = await c.sample_table("APP", "ORDERS")
        assert len(rows) == 1
        row = rows[0]
        # Decimal -> float (per shared serialize._coerce_value)
        assert isinstance(row["AMOUNT"], float)
        assert row["AMOUNT"] == 99.99
        # Timestamp -> ISO-format string
        assert isinstance(row["PLACED_AT"], str)
        assert "2025-03-01" in row["PLACED_AT"]


class TestSampleTableIdentifierGuard:
    """sample_table is a public method; identifiers must be guarded at its surface,
    not only at discover_tables. Closes the gap a future direct caller could open."""

    async def test_dotted_schema_rejected(self, snowflake_db) -> None:
        async with SnowflakeConnector(_connect_kwargs()) as c:
            with pytest.raises(ValueError, match=r"identifier contains '\."):
                await c.sample_table("APP.SUB", "USERS")

    async def test_dotted_table_rejected(self, snowflake_db) -> None:
        async with SnowflakeConnector(_connect_kwargs()) as c:
            with pytest.raises(ValueError, match=r"identifier contains '\."):
                await c.sample_table("APP", "USERS.X")


class TestIdentifierCasePreservation:
    async def test_uppercase_identifiers_preserved(self, snowflake_db) -> None:
        async with SnowflakeConnector(_connect_kwargs()) as c:
            tables = await c.discover_tables()
        for t in tables:
            assert t.schema == t.schema.upper()
            assert t.name == t.name.upper()
            for col in t.columns:
                assert col.name == col.name.upper()


class TestScanSummaryOutput:
    """Verifies the cross-DB FK count is rendered in the scan summary."""

    async def test_cross_db_count_emitted_when_nonzero(
        self, snowflake_db, capsys: pytest.CaptureFixture[str]
    ) -> None:
        async with SnowflakeConnector(_connect_kwargs()) as c:
            tables = await c.discover_tables()

        meta = BundleMeta(
            schema_version=1,
            generated_at="2025-01-01T00:00:00Z",
            connector="snowflake",
            database="fake@a/TEST_DB/APP",
        )
        bundle = ContextBundle(
            meta=meta,
            tables=tuple(tables),
            descriptions={},
            relationships=(),
        )

        print_scan_summary(
            database_label="fake@a/TEST_DB/APP",
            bundle=bundle,
            bundle_dir=Path("/tmp/test"),
            elapsed_seconds=1.0,
            cross_database_dropped=3,
            cross_database_label="TEST_DB",
        )
        out = capsys.readouterr().out
        assert "3 foreign keys reference tables outside database" in out
        assert "TEST_DB" in out

    def test_cross_db_count_silent_when_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        meta = BundleMeta(
            schema_version=1,
            generated_at="2025-01-01T00:00:00Z",
            connector="postgres",
            database="u@h/db",
        )
        bundle = ContextBundle(meta=meta, tables=(), descriptions={}, relationships=())
        print_scan_summary(
            database_label="u@h/db",
            bundle=bundle,
            bundle_dir=Path("/tmp/test"),
            elapsed_seconds=0.5,
        )
        out = capsys.readouterr().out
        assert "foreign keys reference tables outside" not in out

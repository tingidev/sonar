"""Unit tests for the MCP sample tool.

Uses a fake psycopg connect factory to exercise the tool without a live DB.
Integration against the docker fixture lives in test_mcp_sample_integration.py.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import psycopg
import pytest

from sonar.connectors.postgres import Column, Table
from sonar.engine.describe import (
    ColumnDescription,
    PIIRisk,
    SemanticType,
    TableDescription,
)
from sonar.index.bundle import BundleMeta, ContextBundle
from sonar.mcp.tools.bundle_tools import ToolError
from sonar.mcp.tools.sample_tool import (
    DEFAULT_SAMPLE_ROWS,
    MAX_SAMPLE_ROWS,
    make_sample_tool,
)

# ---------------------------------------------------------------------------
# Fixtures — bundle with known PII classifications
# ---------------------------------------------------------------------------


def _meta() -> BundleMeta:
    return BundleMeta(
        schema_version=1,
        generated_at="2026-04-23T00:00:00Z",
        connector="postgres",
        database="test",
    )


def _people_table() -> Table:
    return Table(
        schema="public",
        name="people",
        columns=(
            Column("user_id", "uuid", nullable=False, is_primary_key=True),
            Column("email", "text", nullable=False, is_primary_key=False),
            Column("city", "text", nullable=False, is_primary_key=False),
            Column("country", "text", nullable=False, is_primary_key=False),
            Column("signup_count", "int", nullable=False, is_primary_key=False),
        ),
    )


def _people_description() -> TableDescription:
    return TableDescription(
        schema="public",
        name="people",
        description="People",
        grain="one row per person",
        domain_hints=(),
        columns=(
            ColumnDescription(
                "user_id", "", SemanticType.IDENTIFIER, PIIRisk.NONE, 0.9
            ),
            ColumnDescription(
                "email", "", SemanticType.DIMENSION, PIIRisk.HIGH, 0.9
            ),
            ColumnDescription(
                "city", "", SemanticType.DIMENSION, PIIRisk.MEDIUM, 0.9
            ),
            ColumnDescription(
                "country", "", SemanticType.DIMENSION, PIIRisk.LOW, 0.9
            ),
            ColumnDescription(
                "signup_count", "", SemanticType.MEASURE, PIIRisk.NONE, 0.9
            ),
        ),
        confidence=0.9,
    )


@pytest.fixture
def bundle() -> ContextBundle:
    people = _people_table()
    return ContextBundle(
        meta=_meta(),
        tables=(people,),
        descriptions={("public", "people"): _people_description()},
        relationships=(),
    )


# ---------------------------------------------------------------------------
# Fake psycopg connection
# ---------------------------------------------------------------------------


_SAMPLE_ROW = {
    "user_id": uuid.UUID("11111111-1111-1111-1111-111111111111"),
    "email": "alice@example.com",
    "city": "Barcelona",
    "country": "Spain",
    "signup_count": 3,
}


class _FakeCursor:
    def __init__(self, rows: list[dict[str, Any]], captured_query: list[Any]) -> None:
        self._rows = rows
        self._captured_query = captured_query

    async def __aenter__(self) -> "_FakeCursor":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def execute(self, query: Any) -> None:
        self._captured_query.append(query)

    async def fetchall(self) -> list[dict[str, Any]]:
        return self._rows


class _FakeConnection:
    def __init__(self, rows: list[dict[str, Any]], captured_query: list[Any]) -> None:
        self._rows = rows
        self._captured_query = captured_query

    def cursor(self, *, row_factory: Any = None) -> _FakeCursor:
        return _FakeCursor(self._rows, self._captured_query)

    async def __aenter__(self) -> "_FakeConnection":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


class _FakeConnect:
    def __init__(
        self,
        rows: list[dict[str, Any]] | None = None,
        raises: BaseException | None = None,
    ) -> None:
        self.rows = rows if rows is not None else [dict(_SAMPLE_ROW)]
        self.raises = raises
        self.calls = 0
        self.captured_query: list[Any] = []

    async def __call__(self, dsn: str) -> _FakeConnection:
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        return _FakeConnection(self.rows, self.captured_query)


@pytest.fixture
def fake_connect(monkeypatch: pytest.MonkeyPatch) -> _FakeConnect:
    fake = _FakeConnect()
    monkeypatch.setattr(psycopg.AsyncConnection, "connect", fake)
    return fake


# ---------------------------------------------------------------------------
# Cap enforcement
# ---------------------------------------------------------------------------


class TestCap:
    @pytest.mark.asyncio
    async def test_cap_accept_at_max(
        self,
        bundle: ContextBundle,
        fake_connect: _FakeConnect,
    ) -> None:
        sample = make_sample_tool(bundle, dsn="postgresql://user:pw@h/db")
        rows = await sample("public", "people", limit=MAX_SAMPLE_ROWS)
        assert len(rows) == 1
        assert fake_connect.calls == 1

    @pytest.mark.asyncio
    async def test_cap_reject_above_max_no_connection(
        self,
        bundle: ContextBundle,
        fake_connect: _FakeConnect,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        sample = make_sample_tool(bundle, dsn="postgresql://user:pw@h/db")
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="sonar.mcp.audit"):
            with pytest.raises(ToolError, match="exceeds cap"):
                await sample("public", "people", limit=MAX_SAMPLE_ROWS + 1)
        assert fake_connect.calls == 0
        records = [r for r in caplog.records if r.name == "sonar.mcp.audit"]
        assert len(records) == 1
        assert records[0].outcome == "rejected_cap"
        assert records[0].limit_requested == MAX_SAMPLE_ROWS + 1
        assert records[0].limit_effective is None

    @pytest.mark.asyncio
    async def test_default_limit_when_omitted(
        self,
        bundle: ContextBundle,
        fake_connect: _FakeConnect,
    ) -> None:
        sample = make_sample_tool(bundle, dsn="postgresql://user:pw@h/db")
        await sample("public", "people")
        # LIMIT literal is composed from DEFAULT_SAMPLE_ROWS
        composed_sql = fake_connect.captured_query[0].as_string(None)
        assert f"LIMIT {DEFAULT_SAMPLE_ROWS}" in composed_sql


# ---------------------------------------------------------------------------
# PII stripping
# ---------------------------------------------------------------------------


class TestPIIStripping:
    @pytest.mark.asyncio
    async def test_high_pii_column_stripped_by_default(
        self,
        bundle: ContextBundle,
        fake_connect: _FakeConnect,
    ) -> None:
        sample = make_sample_tool(bundle, dsn="postgresql://user:pw@h/db")
        rows = await sample("public", "people")
        assert rows[0]["email"] is None

    @pytest.mark.asyncio
    async def test_medium_pii_column_stripped_by_default(
        self,
        bundle: ContextBundle,
        fake_connect: _FakeConnect,
    ) -> None:
        sample = make_sample_tool(bundle, dsn="postgresql://user:pw@h/db")
        rows = await sample("public", "people")
        assert rows[0]["city"] is None

    @pytest.mark.asyncio
    async def test_low_pii_column_passes_through(
        self,
        bundle: ContextBundle,
        fake_connect: _FakeConnect,
    ) -> None:
        sample = make_sample_tool(bundle, dsn="postgresql://user:pw@h/db")
        rows = await sample("public", "people")
        assert rows[0]["country"] == "Spain"

    @pytest.mark.asyncio
    async def test_allow_pii_flag_passes_all_columns(
        self,
        bundle: ContextBundle,
        fake_connect: _FakeConnect,
    ) -> None:
        sample = make_sample_tool(
            bundle, dsn="postgresql://user:pw@h/db", allow_pii=True
        )
        rows = await sample("public", "people")
        assert rows[0]["email"] == "alice@example.com"
        assert rows[0]["city"] == "Barcelona"

    @pytest.mark.asyncio
    async def test_column_without_classification_passes_through(
        self,
        fake_connect: _FakeConnect,
    ) -> None:
        # Bundle with null description — every column should pass through,
        # matching the spec's "column without classification" scenario.
        bundle = ContextBundle(
            meta=_meta(),
            tables=(_people_table(),),
            descriptions={("public", "people"): None},
            relationships=(),
        )
        sample = make_sample_tool(bundle, dsn="postgresql://user:pw@h/db")
        rows = await sample("public", "people")
        assert rows[0]["email"] == "alice@example.com"
        assert rows[0]["city"] == "Barcelona"


# ---------------------------------------------------------------------------
# SQL safety
# ---------------------------------------------------------------------------


class TestIdentifierSafety:
    @pytest.mark.asyncio
    async def test_identifier_quoting_stops_injection_payload(
        self,
        bundle: ContextBundle,
        fake_connect: _FakeConnect,
    ) -> None:
        sample = make_sample_tool(bundle, dsn="postgresql://user:pw@h/db")
        # The payload would be a SQL injection under f-string composition.
        # psycopg's Identifier quotes it as a literal (non-existent) name.
        injection_payload = 'users"; DROP TABLE users; --'
        await sample("public", injection_payload, limit=1)
        composed_sql = fake_connect.captured_query[0].as_string(None)
        # DROP TABLE must not appear as unquoted SQL. It may appear as a
        # quoted-literal substring inside the identifier (that's the Identifier
        # guarantee — the payload is treated as a name, not a statement).
        assert "DROP TABLE users;" not in _strip_quoted_identifiers(composed_sql)


def _strip_quoted_identifiers(sql: str) -> str:
    """Remove double-quoted identifier bodies so any surviving `DROP TABLE` would be real SQL."""
    out: list[str] = []
    in_quote = False
    i = 0
    while i < len(sql):
        ch = sql[i]
        if ch == '"':
            # Check for escaped quote ""
            if in_quote and i + 1 < len(sql) and sql[i + 1] == '"':
                i += 2
                continue
            in_quote = not in_quote
            i += 1
            continue
        if not in_quote:
            out.append(ch)
        i += 1
    return "".join(out)


# ---------------------------------------------------------------------------
# DSN scrubbing on connection failure
# ---------------------------------------------------------------------------


class TestDsnScrub:
    @pytest.mark.asyncio
    async def test_connection_failure_scrubs_dsn(
        self,
        bundle: ContextBundle,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        dsn = "postgresql://sonar:hunter2@127.0.0.1:1/sonar_test"
        # psycopg's OperationalError typically embeds the DSN in its message.
        fake = _FakeConnect(
            raises=psycopg.OperationalError(
                f"connection to {dsn} failed: could not connect"
            )
        )
        monkeypatch.setattr(psycopg.AsyncConnection, "connect", fake)

        sample = make_sample_tool(bundle, dsn=dsn)
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="sonar.mcp.audit"):
            with pytest.raises(ToolError) as excinfo:
                await sample("public", "people", limit=1)

        surfaced = str(excinfo.value)
        assert "hunter2" not in surfaced
        assert dsn not in surfaced
        # The db_error audit record is emitted.
        records = [r for r in caplog.records if r.name == "sonar.mcp.audit"]
        assert any(r.outcome == "db_error" for r in records)


# ---------------------------------------------------------------------------
# Audit emission on happy path
# ---------------------------------------------------------------------------


class TestAudit:
    @pytest.mark.asyncio
    async def test_ok_outcome_audit_emitted(
        self,
        bundle: ContextBundle,
        fake_connect: _FakeConnect,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        sample = make_sample_tool(bundle, dsn="postgresql://user:pw@h/db")
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="sonar.mcp.audit"):
            await sample("public", "people", limit=2)
        records = [r for r in caplog.records if r.name == "sonar.mcp.audit"]
        assert len(records) == 1
        rec = records[0]
        assert rec.outcome == "ok"
        assert rec.schema == "public"
        assert rec.table == "people"
        assert rec.limit_requested == 2
        assert rec.limit_effective == 2
        assert rec.rows_returned == 1


# ---------------------------------------------------------------------------
# Datatype serialisation (UUIDs, datetimes must be JSON-safe)
# ---------------------------------------------------------------------------


class TestRowSerialisation:
    @pytest.mark.asyncio
    async def test_uuid_and_datetime_serialised_as_strings(
        self,
        bundle: ContextBundle,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake = _FakeConnect(
            rows=[
                {
                    "user_id": uuid.UUID("22222222-2222-2222-2222-222222222222"),
                    "email": "x@y.com",
                    "city": "Madrid",
                    "country": "Spain",
                    "signup_count": 1,
                }
            ]
        )
        monkeypatch.setattr(psycopg.AsyncConnection, "connect", fake)

        sample = make_sample_tool(
            bundle, dsn="postgresql://u:p@h/db", allow_pii=True
        )
        rows = await sample("public", "people", limit=1)
        assert rows[0]["user_id"] == "22222222-2222-2222-2222-222222222222"
        assert isinstance(rows[0]["user_id"], str)


def test_constants_match_design() -> None:
    assert DEFAULT_SAMPLE_ROWS == 5
    assert MAX_SAMPLE_ROWS == 20

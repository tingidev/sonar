"""Integration test for the MCP sample tool against the docker fixture DB.

Requires: `docker compose up -d sonar-test-postgres` with the seed in
`tests/fixtures/init.sql`. Uses the `TEST_DATABASE_URL` env var or a default.
"""

from __future__ import annotations

import os

import pytest

from sonar.connectors.types import Column, Table
from sonar.engine.describe import (
    ColumnDescription,
    PIIRisk,
    SemanticType,
    TableDescription,
)
from sonar.index.bundle import BundleMeta, ContextBundle
from sonar.mcp.tools.sample_tool import make_sample_tool

DEFAULT_TEST_DATABASE_URL = "postgresql://sonar:sonar@localhost:5433/sonar_test"


def _test_dsn() -> str:
    return os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)


def _users_bundle() -> ContextBundle:
    users = Table(
        schema="public",
        name="users",
        columns=(
            Column("user_id", "uuid", nullable=False, is_primary_key=True),
            Column("email", "text", nullable=False, is_primary_key=False),
            Column("name", "text", nullable=False, is_primary_key=False),
            Column(
                "created_at",
                "timestamptz",
                nullable=False,
                is_primary_key=False,
            ),
        ),
    )
    description = TableDescription(
        schema="public",
        name="users",
        description="Registered users",
        grain="one row per user",
        domain_hints=(),
        columns=(
            ColumnDescription("user_id", "", SemanticType.IDENTIFIER, PIIRisk.NONE, 0.9),
            ColumnDescription("email", "", SemanticType.DIMENSION, PIIRisk.HIGH, 0.9),
            ColumnDescription("name", "", SemanticType.DIMENSION, PIIRisk.HIGH, 0.9),
            ColumnDescription("created_at", "", SemanticType.DIMENSION, PIIRisk.NONE, 0.9),
        ),
        confidence=0.9,
    )
    return ContextBundle(
        meta=BundleMeta(
            schema_version=1,
            generated_at="2026-04-23T00:00:00Z",
            connector="postgres",
            database="test",
        ),
        tables=(users,),
        descriptions={("public", "users"): description},
        relationships=(),
    )


@pytest.mark.integration
class TestSampleIntegration:
    @pytest.mark.asyncio
    async def test_sample_happy_path_with_pii_stripping(self) -> None:
        bundle = _users_bundle()
        sample = make_sample_tool(bundle, dsn=_test_dsn())

        rows = await sample("public", "users", limit=3)

        assert len(rows) == 3
        for row in rows:
            # user_id is not PII -> passes through
            assert row["user_id"] is not None
            assert isinstance(row["user_id"], str)
            # email and name are HIGH -> stripped
            assert row["email"] is None
            assert row["name"] is None
            # created_at is not PII -> passes through
            assert row["created_at"] is not None

    @pytest.mark.asyncio
    async def test_sample_allow_pii_returns_raw(self) -> None:
        bundle = _users_bundle()
        sample = make_sample_tool(bundle, dsn=_test_dsn(), allow_pii=True)

        rows = await sample("public", "users", limit=3)

        emails = {row["email"] for row in rows}
        assert emails == {
            "alice@example.com",
            "bob@example.com",
            "carol@example.com",
        }

"""Unit tests for the bundle-backed MCP tools (discover/describe/relationships/search)."""

from __future__ import annotations

import pytest

from sonar.connectors.postgres import Column, Table
from sonar.engine.describe import (
    ColumnDescription,
    PIIRisk,
    SemanticType,
    TableDescription,
)
from sonar.index.bundle import BundleMeta, ContextBundle
from sonar.mcp.tools.bundle_tools import (
    ToolError,
    describe_tool,
    discover_tool,
    relationships_tool,
    search_tool,
)
from sonar.relationships import Relationship, RelationshipKind


def _table(schema: str, name: str, *columns: Column, row_count: int | None = None) -> Table:
    return Table(schema=schema, name=name, columns=tuple(columns), row_count=row_count)


def _desc(
    schema: str,
    name: str,
    *,
    description: str = "",
    grain: str = "",
    columns: tuple[ColumnDescription, ...] = (),
    domain_hints: tuple[str, ...] = (),
    confidence: float = 0.9,
) -> TableDescription:
    return TableDescription(
        schema=schema,
        name=name,
        description=description,
        grain=grain,
        domain_hints=domain_hints,
        columns=columns,
        confidence=confidence,
    )


def _col_desc(
    name: str,
    *,
    description: str = "",
    semantic_type: SemanticType = SemanticType.DIMENSION,
    pii_risk: PIIRisk = PIIRisk.NONE,
    confidence: float = 0.9,
) -> ColumnDescription:
    return ColumnDescription(
        name=name,
        description=description,
        semantic_type=semantic_type,
        pii_risk=pii_risk,
        confidence=confidence,
    )


def _meta() -> BundleMeta:
    return BundleMeta(
        schema_version=1,
        generated_at="2026-04-23T00:00:00Z",
        connector="postgres",
        database="test",
    )


@pytest.fixture
def bundle() -> ContextBundle:
    users = _table(
        "public",
        "users",
        Column("user_id", "uuid", nullable=False, is_primary_key=True),
        Column("email", "text", nullable=False, is_primary_key=False),
        row_count=42,
    )
    orders = _table(
        "public",
        "orders",
        Column("order_id", "uuid", nullable=False, is_primary_key=True),
        Column("user_id", "uuid", nullable=False, is_primary_key=False),
        row_count=None,
    )
    audits = _table(
        "analytics",
        "audit_events",
        Column("event_id", "uuid", nullable=False, is_primary_key=True),
        row_count=100,
    )

    descriptions = {
        ("public", "users"): _desc(
            "public",
            "users",
            description="Registered customers",
            grain="one row per customer",
            columns=(
                _col_desc(
                    "user_id",
                    description="Surrogate customer ID",
                    semantic_type=SemanticType.IDENTIFIER,
                ),
                _col_desc(
                    "email",
                    description="Customer email address",
                    pii_risk=PIIRisk.HIGH,
                ),
            ),
        ),
        ("public", "orders"): None,
        ("analytics", "audit_events"): _desc(
            "analytics",
            "audit_events",
            description="Pharmaceutical compliance audit log",
            grain="one row per audited event",
            columns=(
                _col_desc(
                    "event_id",
                    description="Audit event surrogate",
                    semantic_type=SemanticType.IDENTIFIER,
                ),
            ),
        ),
    }

    relationships = (
        Relationship(
            source_schema="public",
            source_table="orders",
            source_column="user_id",
            target_schema="public",
            target_table="users",
            target_column="user_id",
            kind=RelationshipKind.DECLARED,
        ),
    )

    return ContextBundle(
        meta=_meta(),
        tables=(users, orders, audits),
        descriptions=descriptions,
        relationships=relationships,
    )


class TestDiscover:
    def test_unfiltered_returns_every_table(self, bundle: ContextBundle) -> None:
        result = discover_tool(bundle)
        assert len(result) == 3
        assert {(r["schema"], r["name"]) for r in result} == {
            ("public", "users"),
            ("public", "orders"),
            ("analytics", "audit_events"),
        }

    def test_row_count_is_included_including_null(self, bundle: ContextBundle) -> None:
        result = discover_tool(bundle)
        by_name = {(r["schema"], r["name"]): r for r in result}
        assert by_name[("public", "users")]["row_count"] == 42
        assert by_name[("public", "orders")]["row_count"] is None

    def test_schema_filter_restricts_to_matching_schema(
        self, bundle: ContextBundle
    ) -> None:
        result = discover_tool(bundle, schema="public")
        assert {(r["schema"], r["name"]) for r in result} == {
            ("public", "users"),
            ("public", "orders"),
        }

    def test_schema_filter_no_match_returns_empty_list(
        self, bundle: ContextBundle
    ) -> None:
        assert discover_tool(bundle, schema="ghost") == []


class TestDescribe:
    def test_successful_describe_joins_tables_and_descriptions(
        self, bundle: ContextBundle
    ) -> None:
        result = describe_tool(bundle, "public", "users")
        assert result["schema"] == "public"
        assert result["name"] == "users"
        assert result["description"] == "Registered customers"
        assert result["grain"] == "one row per customer"
        by_name = {c["name"]: c for c in result["columns"]}
        assert by_name["email"]["pii_risk"] == "high"
        assert by_name["email"]["semantic_type"] == "dimension"
        assert by_name["user_id"]["is_primary_key"] is True

    def test_null_description_returns_raw_column_shape(
        self, bundle: ContextBundle
    ) -> None:
        result = describe_tool(bundle, "public", "orders")
        assert result["schema"] == "public"
        assert result["name"] == "orders"
        assert result["description"] is None
        assert result["grain"] is None
        assert result["domain_hints"] is None
        assert result["confidence"] is None
        for col in result["columns"]:
            assert col["description"] is None
            assert col["semantic_type"] is None
            assert col["pii_risk"] is None
            assert col["confidence"] is None
            assert col["data_type"] in ("uuid",)
            assert "is_primary_key" in col

    def test_unknown_table_raises_tool_error(self, bundle: ContextBundle) -> None:
        with pytest.raises(ToolError, match="unknown table"):
            describe_tool(bundle, "public", "missing")


class TestRelationships:
    def test_outgoing_only_returns_edges_from_target(
        self, bundle: ContextBundle
    ) -> None:
        edges = relationships_tool(bundle, "public", "orders", direction="outgoing")
        assert len(edges) == 1
        assert edges[0]["source_table"] == "orders"
        assert edges[0]["target_table"] == "users"

    def test_incoming_only_returns_edges_to_target(
        self, bundle: ContextBundle
    ) -> None:
        edges = relationships_tool(bundle, "public", "users", direction="incoming")
        assert len(edges) == 1
        assert edges[0]["source_table"] == "orders"
        assert edges[0]["target_table"] == "users"

    def test_both_direction_is_default(self, bundle: ContextBundle) -> None:
        edges = relationships_tool(bundle, "public", "users")
        assert len(edges) == 1

    def test_table_with_no_relationships_returns_empty_list(
        self, bundle: ContextBundle
    ) -> None:
        assert relationships_tool(bundle, "analytics", "audit_events") == []

    def test_invalid_direction_raises_tool_error(self, bundle: ContextBundle) -> None:
        with pytest.raises(ToolError, match="invalid direction"):
            relationships_tool(bundle, "public", "users", direction="sideways")


class TestSearch:
    def test_table_name_match(self, bundle: ContextBundle) -> None:
        result = search_tool(bundle, "users")
        assert any(
            r["table"] == "users" and r["match_type"] == "table_name" for r in result
        )

    def test_column_name_match(self, bundle: ContextBundle) -> None:
        result = search_tool(bundle, "order_id")
        assert any(
            r["table"] == "orders" and r["match_type"] == "column_name" for r in result
        )

    def test_description_body_match(self, bundle: ContextBundle) -> None:
        result = search_tool(bundle, "Pharmaceutical")
        assert any(
            r["table"] == "audit_events" and r["match_type"] == "description_body"
            for r in result
        )

    def test_ranking_tier_order_table_then_column_then_body(
        self, bundle: ContextBundle
    ) -> None:
        # "email" matches the `email` column name on users AND the users
        # description body ("Customer email address"). No table name matches.
        # Column-name wins the tier — confirms ranking prefers column over body.
        result = search_tool(bundle, "email")
        assert result
        first = result[0]
        assert first["table"] == "users"
        assert first["match_type"] == "column_name"

    def test_case_insensitive(self, bundle: ContextBundle) -> None:
        assert search_tool(bundle, "USERS")
        assert search_tool(bundle, "pharmaceutical")

    def test_limit_enforcement(self, bundle: ContextBundle) -> None:
        result = search_tool(bundle, "e", limit=1)
        assert len(result) == 1

    def test_empty_query_returns_empty_list(self, bundle: ContextBundle) -> None:
        assert search_tool(bundle, "") == []

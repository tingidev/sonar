"""Tests for the eval artifact and sample selection helpers."""

from __future__ import annotations

from sonar.connectors.types import Column, Table
from sonar.engine.describe import (
    ColumnDescription,
    PIIRisk,
    SemanticType,
    TableDescription,
)
from sonar.eval._artifact import (
    build_artifact,
    prompt_version_hash,
    select_sample_tables,
)
from sonar.eval.descriptions import DescriptionQualityReport, TableScore
from sonar.index.bundle import BundleMeta, ContextBundle


def _t(schema: str, name: str) -> Table:
    return Table(schema=schema, name=name, columns=(Column("id", "int", False, True),))


def _desc(schema: str, name: str) -> TableDescription:
    return TableDescription(
        schema=schema,
        name=name,
        description="d",
        grain="g",
        domain_hints=(),
        columns=(
            ColumnDescription(
                name="id",
                description="pk",
                semantic_type=SemanticType.IDENTIFIER,
                pii_risk=PIIRisk.NONE,
                confidence=0.9,
            ),
        ),
        confidence=0.9,
    )


def _bundle(tables: list[Table], described: set[tuple[str, str]] | None = None) -> ContextBundle:
    if described is None:
        described = {(t.schema, t.name) for t in tables}
    descriptions: dict[tuple[str, str], TableDescription | None] = {}
    for t in tables:
        key = (t.schema, t.name)
        descriptions[key] = _desc(t.schema, t.name) if key in described else None
    meta = BundleMeta(
        schema_version=1,
        generated_at="2026-01-01T00:00:00Z",
        connector="postgres",
        database="x",
    )
    return ContextBundle(
        meta=meta,
        tables=tuple(tables),
        descriptions=descriptions,
        relationships=(),
    )


class TestSelectSampleTables:
    def test_round_robin_across_schemas(self) -> None:
        tables = [
            _t("a", "t1"), _t("a", "t2"), _t("a", "t3"),
            _t("b", "t1"), _t("b", "t2"), _t("b", "t3"),
        ]
        selection = select_sample_tables(_bundle(tables), 4)
        # Sorted schema order: a, b. Interleave: a.t1, b.t1, a.t2, b.t2.
        keys = [(t.schema, t.name) for t in selection]
        assert keys == [("a", "t1"), ("b", "t1"), ("a", "t2"), ("b", "t2")]

    def test_round_robin_handles_uneven_schemas(self) -> None:
        big = [_t("big", f"t{i:02d}") for i in range(50)]
        small_a = [_t("sa", "ta1"), _t("sa", "ta2")]
        small_b = [_t("sb", "tb1"), _t("sb", "tb2")]
        bundle = _bundle(big + small_a + small_b)
        selection = select_sample_tables(bundle, 10)
        keys = [(t.schema, t.name) for t in selection]
        # Both small schemas should contribute both their tables (since 10 > 2*3
        # rounds). The remaining 6 come from big in sorted order.
        small_count = sum(1 for k in keys if k[0] in ("sa", "sb"))
        big_count = sum(1 for k in keys if k[0] == "big")
        assert small_count == 4
        assert big_count == 6
        # Big-schema selection is the first 6 in sorted order.
        big_keys = [k[1] for k in keys if k[0] == "big"]
        assert big_keys == sorted(big_keys)
        assert big_keys[0] == "t00"

    def test_single_schema_degenerates_to_sorted_top_n(self) -> None:
        tables = [_t("only", f"t{i:02d}") for i in range(20)]
        selection = select_sample_tables(_bundle(tables), 10)
        names = [t.name for t in selection]
        assert names == [f"t{i:02d}" for i in range(10)]

    def test_sample_larger_than_available_returns_all(self) -> None:
        tables = [_t("a", "t1"), _t("a", "t2"), _t("b", "t1")]
        selection = select_sample_tables(_bundle(tables), 100)
        assert len(selection) == 3

    def test_none_sample_returns_all(self) -> None:
        tables = [_t("a", "t1"), _t("b", "t2")]
        selection = select_sample_tables(_bundle(tables), None)
        assert len(selection) == 2

    def test_deterministic_across_runs(self) -> None:
        tables = [_t("a", "t1"), _t("a", "t2"), _t("b", "t1"), _t("b", "t2")]
        first = select_sample_tables(_bundle(tables), 3)
        second = select_sample_tables(_bundle(tables), 3)
        first_keys = [(t.schema, t.name) for t in first]
        second_keys = [(t.schema, t.name) for t in second]
        assert first_keys == second_keys

    def test_null_descriptions_excluded(self) -> None:
        tables = [_t("a", "t1"), _t("a", "t2"), _t("a", "t3")]
        # Only t1 and t3 have descriptions.
        bundle = _bundle(tables, described={("a", "t1"), ("a", "t3")})
        selection = select_sample_tables(bundle, 5)
        names = [t.name for t in selection]
        assert names == ["t1", "t3"]


class TestPromptVersionHash:
    def test_hash_is_sha256_hex(self) -> None:
        h = prompt_version_hash()
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_hash_stable_within_run(self) -> None:
        assert prompt_version_hash() == prompt_version_hash()


class TestBuildArtifact:
    def test_artifact_shape(self) -> None:
        report = DescriptionQualityReport(
            scored_count=1,
            skipped_null=0,
            total_judge_failures=0,
            mean_accuracy=4.0,
            mean_specificity=3.0,
            mean_domain_inference=4.0,
            flagged=(),
            per_table=(
                TableScore(
                    "public", "users", 4, 3, 4,
                    "schema match", "ok", "auth domain",
                ),
            ),
        )
        evaluated = (_t("public", "users"),)
        payload = build_artifact(
            bundle_dir=".sonar",
            report=report,
            generator_model="anthropic/claude-haiku-4-5-20251001",
            judge_model="gpt-4o",
            evaluated_tables=evaluated,
        )
        assert payload["schema_version"] == 1
        assert payload["generator_model"] == "anthropic/claude-haiku-4-5-20251001"
        assert payload["judge_model"] == "gpt-4o"
        assert payload["evaluated_tables"] == ["public.users"]
        assert payload["metrics"]["scored_count"] == 1
        assert payload["per_table"][0]["accuracy_reasoning"] == "schema match"
        assert "prompt_version_hash" in payload
        # ISO 8601 with Z suffix
        assert payload["run_timestamp"].endswith("Z")

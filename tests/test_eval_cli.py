"""CLI-level tests for `sonar eval`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sonar.cli import main
from sonar.connectors.types import Column, Table
from sonar.engine.describe import (
    ColumnDescription,
    PIIRisk,
    SemanticType,
    TableDescription,
)
from sonar.index.bundle import BundleMeta, ContextBundle
from sonar.index.store import ContextStore
from sonar.relationships import Relationship, RelationshipKind


def _build_bundle() -> ContextBundle:
    tables = (
        Table(schema="public", name="users", columns=(Column("user_id", "uuid", False, True),)),
        Table(
            schema="public",
            name="orders",
            columns=(
                Column("order_id", "int", False, True),
                Column("user_id", "uuid", False, False),
            ),
        ),
    )
    cols_users = (
        ColumnDescription(
            name="user_id",
            description="pk",
            semantic_type=SemanticType.IDENTIFIER,
            pii_risk=PIIRisk.NONE,
            confidence=0.9,
        ),
    )
    cols_orders = (
        ColumnDescription(
            name="order_id",
            description="pk",
            semantic_type=SemanticType.IDENTIFIER,
            pii_risk=PIIRisk.NONE,
            confidence=0.9,
        ),
        ColumnDescription(
            name="user_id",
            description="fk",
            semantic_type=SemanticType.IDENTIFIER,
            pii_risk=PIIRisk.NONE,
            confidence=0.9,
        ),
    )
    descriptions: dict[tuple[str, str], TableDescription | None] = {
        ("public", "users"): TableDescription(
            schema="public",
            name="users",
            description="user accounts",
            grain="one row per user",
            domain_hints=("auth",),
            columns=cols_users,
            confidence=0.9,
        ),
        ("public", "orders"): TableDescription(
            schema="public",
            name="orders",
            description="customer orders",
            grain="one row per order",
            domain_hints=("commerce",),
            columns=cols_orders,
            confidence=0.85,
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
    meta = BundleMeta(
        schema_version=1,
        generated_at="2026-01-01T00:00:00Z",
        connector="postgres",
        database="x",
    )
    return ContextBundle(
        meta=meta,
        tables=tables,
        descriptions=descriptions,
        relationships=relationships,
    )


@pytest.fixture
def bundle_dir(tmp_path: Path) -> Path:
    bundle_dir = tmp_path / "bundle"
    ContextStore(bundle_dir).write(_build_bundle())
    return bundle_dir


class TestEvalQualityCli:
    def test_default_mode_human_output(
        self, bundle_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        exit_code = main(["eval", "--bundle-dir", str(bundle_dir)])
        assert exit_code == 0
        out = capsys.readouterr().out
        assert "Tables: 2" in out
        assert "Description coverage" in out

    def test_default_mode_json_output(
        self, bundle_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        exit_code = main(["eval", "--bundle-dir", str(bundle_dir), "--json"])
        assert exit_code == 0
        out = capsys.readouterr().out
        payload = json.loads(out)
        assert payload["mode"] == "quality"
        assert payload["metrics"]["table_count"] == 2
        assert payload["metrics"]["description_coverage"] == 1.0

    def test_missing_bundle_fails_clean(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        missing = tmp_path / "nope"
        exit_code = main(["eval", "--bundle-dir", str(missing)])
        assert exit_code == 1
        err = capsys.readouterr().err
        assert "no bundle found" in err

    def test_help_lists_modes(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as excinfo:
            main(["eval", "--help"])
        assert excinfo.value.code == 0
        out = capsys.readouterr().out
        for flag in ("--relationships", "--search", "--diff", "--descriptions", "--json"):
            assert flag in out


class TestEvalSearchCli:
    def test_search_mode_runs_against_bundle(
        self, bundle_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        gt = tmp_path / "gt.yaml"
        gt.write_text(
            "queries:\n" "  - query: orders\n" "    expected:\n" "      - public.orders\n",
            encoding="utf-8",
        )
        exit_code = main(
            [
                "eval",
                "--bundle-dir",
                str(bundle_dir),
                "--search",
                str(gt),
                "--json",
            ]
        )
        assert exit_code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["mode"] == "search"
        assert payload["metrics"]["query_count"] == 1
        assert payload["metrics"]["mean_recall_at_k"] == 1.0

    def test_search_invalid_yaml_fails(
        self, bundle_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        gt = tmp_path / "gt.yaml"
        gt.write_text("queries:\n  - {bad}\n", encoding="utf-8")
        exit_code = main(["eval", "--bundle-dir", str(bundle_dir), "--search", str(gt)])
        assert exit_code == 1
        assert "eval failed" in capsys.readouterr().err


class TestEvalDiffCli:
    def test_diff_against_self_is_empty(
        self, bundle_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        exit_code = main(
            [
                "eval",
                "--bundle-dir",
                str(bundle_dir),
                "--diff",
                str(bundle_dir),
                "--json",
            ]
        )
        assert exit_code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["mode"] == "diff"
        assert payload["metrics"]["tables_added"] == 0
        assert payload["metrics"]["tables_removed"] == 0

    def test_diff_missing_other_fails(
        self, bundle_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        missing = tmp_path / "missing"
        exit_code = main(["eval", "--bundle-dir", str(bundle_dir), "--diff", str(missing)])
        assert exit_code == 1
        assert "no bundle found" in capsys.readouterr().err


class TestEvalDescriptionsCli:
    def test_provider_error_returns_clean_stderr(
        self,
        bundle_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        async def _boom(*args, **kwargs):
            raise RuntimeError("api key invalid")

        monkeypatch.setattr("sonar.cli.evaluate_descriptions", _boom, raising=False)
        # Patch the import used inside _run_eval_descriptions.
        import sonar.eval.descriptions as descriptions_mod

        monkeypatch.setattr(descriptions_mod, "evaluate_descriptions", _boom)

        exit_code = main(["eval", "--bundle-dir", str(bundle_dir), "--descriptions"])
        assert exit_code == 1
        err = capsys.readouterr().err
        assert "eval failed" in err
        assert "RuntimeError" in err
        assert "api key invalid" in err

    def test_total_judge_failure_exits_nonzero(
        self,
        bundle_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import sonar.eval.descriptions as descriptions_mod
        from sonar.eval.descriptions import DescriptionQualityReport

        async def _all_fail(*args, **kwargs) -> DescriptionQualityReport:
            return DescriptionQualityReport(
                scored_count=0,
                skipped_null=0,
                judge_failures=2,
                mean_accuracy=0.0,
                mean_completeness=0.0,
                mean_specificity=0.0,
                flagged=(),
                per_table=(),
            )

        monkeypatch.setattr(descriptions_mod, "evaluate_descriptions", _all_fail)

        exit_code = main(["eval", "--bundle-dir", str(bundle_dir), "--descriptions"])
        assert exit_code == 1
        err = capsys.readouterr().err
        assert "judge failed on all 2 tables" in err


class TestEvalModeMutuallyExclusive:
    def test_two_modes_rejected(self, bundle_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit):
            main(
                [
                    "eval",
                    "--bundle-dir",
                    str(bundle_dir),
                    "--diff",
                    str(bundle_dir),
                    "--descriptions",
                ]
            )
        err = capsys.readouterr().err
        assert "not allowed with" in err.lower()

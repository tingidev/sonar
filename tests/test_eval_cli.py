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
                total_judge_failures=2,
                mean_accuracy=0.0,
                mean_specificity=0.0,
                mean_domain_inference=0.0,
                flagged=(),
                per_table=(),
            )

        monkeypatch.setattr(descriptions_mod, "evaluate_descriptions", _all_fail)

        exit_code = main(["eval", "--bundle-dir", str(bundle_dir), "--descriptions"])
        assert exit_code == 1
        err = capsys.readouterr().err
        assert "judge failed on all 2 tables" in err


class TestEvalDescriptionsCliFlags:
    """Coverage for --judge-model, --sample, --output, prompt hash stability."""

    def _stub_evaluate(self, monkeypatch: pytest.MonkeyPatch) -> dict:
        """Replace `evaluate_descriptions` with a stub that records its kwargs
        and returns a deterministic report. Returns the captured-state dict."""
        import sonar.cli as cli_mod
        import sonar.eval.descriptions as descriptions_mod
        from sonar.eval.descriptions import (
            DescriptionQualityReport,
            TableScore,
        )

        captured: dict = {}

        async def _stub(bundle, llm_client, *, config=None, tables=None):
            captured["config_model"] = getattr(config, "model", None)
            captured["tables"] = tables
            scored = (
                TableScore(
                    "public", "users", 4, 4, 4,
                    "matches", "concrete", "auth domain",
                ),
            )
            return DescriptionQualityReport(
                scored_count=1,
                skipped_null=0,
                total_judge_failures=0,
                mean_accuracy=4.0,
                mean_specificity=4.0,
                mean_domain_inference=4.0,
                flagged=(),
                per_table=scored,
            )

        # `_run_eval_descriptions` does a local import of `evaluate_descriptions`,
        # so we have to patch the source module.
        monkeypatch.setattr(descriptions_mod, "evaluate_descriptions", _stub)
        monkeypatch.setattr(cli_mod, "create_llm_client", lambda config: object())
        return captured

    def test_judge_model_routes_through_separate_client(
        self,
        bundle_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import sonar.cli as cli_mod
        from sonar.engine.llm import LLMConfig

        captured = self._stub_evaluate(monkeypatch)
        configs_seen: list[LLMConfig] = []

        def _fake_create(config: LLMConfig) -> object:
            configs_seen.append(config)
            return object()

        monkeypatch.setattr(cli_mod, "create_llm_client", _fake_create)

        exit_code = main(
            [
                "eval",
                "--bundle-dir",
                str(bundle_dir),
                "--descriptions",
                "--model",
                "anthropic/claude-haiku-4-5-20251001",
                "--judge-model",
                "gpt-4o",
            ]
        )
        assert exit_code == 0
        # Judge client created from --judge-model; the eval pipeline only uses
        # the judge client (the generator is whatever wrote the bundle).
        assert len(configs_seen) == 1
        assert configs_seen[0].model == "gpt-4o"
        assert captured["config_model"] == "gpt-4o"

    def test_judge_model_defaults_to_model(
        self,
        bundle_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import sonar.cli as cli_mod
        from sonar.engine.llm import LLMConfig

        self._stub_evaluate(monkeypatch)
        configs_seen: list[LLMConfig] = []
        monkeypatch.setattr(
            cli_mod, "create_llm_client", lambda c: (configs_seen.append(c), object())[1]
        )

        exit_code = main(
            [
                "eval",
                "--bundle-dir",
                str(bundle_dir),
                "--descriptions",
                "--model",
                "anthropic/claude-haiku-4-5-20251001",
            ]
        )
        assert exit_code == 0
        assert configs_seen[0].model == "anthropic/claude-haiku-4-5-20251001"

    def test_sample_size_passed_to_evaluator(
        self,
        bundle_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        captured = self._stub_evaluate(monkeypatch)
        exit_code = main(
            [
                "eval",
                "--bundle-dir",
                str(bundle_dir),
                "--descriptions",
                "--sample",
                "1",
            ]
        )
        assert exit_code == 0
        tables = captured["tables"]
        assert tables is not None
        assert len(tables) == 1

    def test_output_writes_versioned_artifact(
        self,
        bundle_dir: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        self._stub_evaluate(monkeypatch)
        out_path = tmp_path / "run-001.json"
        exit_code = main(
            [
                "eval",
                "--bundle-dir",
                str(bundle_dir),
                "--descriptions",
                "--output",
                str(out_path),
            ]
        )
        assert exit_code == 0
        assert out_path.exists()
        payload = json.loads(out_path.read_text(encoding="utf-8"))
        assert payload["schema_version"] == 1
        assert "run_timestamp" in payload
        assert "prompt_version_hash" in payload
        assert len(payload["prompt_version_hash"]) == 64
        assert payload["metrics"]["scored_count"] == 1
        per_table = payload["per_table"]
        assert per_table[0]["accuracy_reasoning"] == "matches"
        assert payload["evaluated_tables"]  # non-empty

    def test_no_output_means_no_artifact(
        self,
        bundle_dir: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        self._stub_evaluate(monkeypatch)
        exit_code = main(
            [
                "eval",
                "--bundle-dir",
                str(bundle_dir),
                "--descriptions",
            ]
        )
        assert exit_code == 0
        # No file written anywhere on disk (the tmp_path is unused).
        assert list(tmp_path.glob("*.json")) == []

    def test_prompt_hash_stable_across_calls(self) -> None:
        from sonar.eval._artifact import prompt_version_hash

        first = prompt_version_hash()
        second = prompt_version_hash()
        assert first == second
        assert len(first) == 64


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

"""Integration tests for the end-to-end `sonar scan` CLI pipeline."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

import sonar.cli
from sonar.cli import main
from sonar.engine.llm import LLMClient
from sonar.index.store import ContextStore

DEFAULT_TEST_DATABASE_URL = "postgresql://sonar:sonar@localhost:5433/sonar_test"


def _test_dsn() -> str:
    return os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)


_TABLE_LINE = re.compile(r"^Table:\s+(\S+)\.(\S+)\s*$", re.MULTILINE)
_COLUMN_LINE = re.compile(r"^\s*-\s+(\S+?):\s+.*pk=(true|false)\s*$", re.MULTILINE)


def _payload_from_prompt(prompt: str) -> str:
    """Parse the prompt's columns block and emit a valid description JSON.

    The prompt shape is defined in `sonar.engine._prompts.build_table_prompt`.
    We extract the column names + pk flags from the prompt so this fake does
    not need advance knowledge of the fixture schema.
    """
    match = _TABLE_LINE.search(prompt)
    if match is None:
        raise AssertionError(f"Could not find 'Table:' in prompt: {prompt[:200]}")
    schema, name = match.group(1), match.group(2)

    columns_payload = []
    for col_match in _COLUMN_LINE.finditer(prompt):
        col_name = col_match.group(1)
        is_pk = col_match.group(2) == "true"
        columns_payload.append(
            {
                "name": col_name,
                "description": f"Column {col_name}",
                "semantic_type": "identifier" if is_pk else "dimension",
                "pii_risk": "none",
                "confidence": 0.8,
            }
        )

    return json.dumps(
        {
            "description": f"Fixture table {schema}.{name}",
            "grain": f"one row per {name}",
            "domain_hints": ["test"],
            "columns": columns_payload,
            "confidence": 0.9,
        }
    )


class _FakeLLMClient(LLMClient):
    """Parses the prompt shape to emit a valid payload. Failures configurable per table."""

    def __init__(self, fail: set[tuple[str, str]] | None = None) -> None:
        self._fail = fail or set()

    async def generate(self, prompt: str, system: str | None = None) -> str:
        match = _TABLE_LINE.search(prompt)
        if match and (match.group(1), match.group(2)) in self._fail:
            return "this is not a valid JSON object"
        return _payload_from_prompt(prompt)


def _install_fake_client(
    monkeypatch: pytest.MonkeyPatch,
    fail: set[tuple[str, str]] | None = None,
) -> None:
    failures = fail or set()

    def _factory(*_args: object, **_kwargs: object) -> _FakeLLMClient:
        return _FakeLLMClient(failures)

    monkeypatch.setattr(sonar.cli, "create_llm_client", _factory)


@pytest.mark.integration
class TestScanCLI:
    def test_successful_scan_writes_full_bundle(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_fake_client(monkeypatch)

        bundle_dir = tmp_path / "bundle"
        exit_code = main(["scan", _test_dsn(), "--bundle-dir", str(bundle_dir)])

        assert exit_code == 0
        for fname in ("meta.json", "tables.json", "descriptions.json", "relationships.json"):
            assert (bundle_dir / fname).exists(), fname

        bundle = ContextStore(bundle_dir).read()
        assert bundle is not None
        assert len(bundle.tables) >= 1
        assert all(v is not None for v in bundle.descriptions.values())
        assert len(bundle.relationships) >= 1
        assert bundle.meta.connector == "postgres"
        assert "sonar" not in bundle.meta.database or "secret" not in bundle.meta.database

    def test_partial_failure_persists_null(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_fake_client(monkeypatch, fail={("public", "orders")})

        bundle_dir = tmp_path / "bundle"
        exit_code = main(["scan", _test_dsn(), "--bundle-dir", str(bundle_dir)])

        assert exit_code == 0
        raw = json.loads((bundle_dir / "descriptions.json").read_text())
        assert raw["public.orders"] is None
        present = [k for k, v in raw.items() if v is not None]
        assert len(present) >= 1

        bundle = ContextStore(bundle_dir).read()
        assert bundle is not None
        assert bundle.descriptions[("public", "orders")] is None

    def test_url_alias_accepted(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_client(monkeypatch)

        bundle_dir = tmp_path / "bundle"
        exit_code = main(["scan", "--url", _test_dsn(), "--bundle-dir", str(bundle_dir)])

        assert exit_code == 0
        assert (bundle_dir / "meta.json").exists()


class TestScanCLIFailures:
    def test_unreachable_db_exits_nonzero_and_writes_nothing(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _install_fake_client(monkeypatch)

        bundle_dir = tmp_path / "bundle"
        unreachable = "postgresql://sonar:hunter2@127.0.0.1:1/sonar_test"
        exit_code = main(["scan", unreachable, "--bundle-dir", str(bundle_dir)])

        assert exit_code == 1
        captured = capsys.readouterr()
        assert captured.err.strip().startswith("scan failed:")
        # The full DSN (and especially the password) must never land on stderr,
        # even though psycopg embeds the connection string in its error message.
        assert "hunter2" not in captured.err
        assert unreachable not in captured.err
        assert not bundle_dir.exists()

    def test_missing_dsn_exits_nonzero(self, capsys: pytest.CaptureFixture[str]) -> None:
        exit_code = main(["scan"])
        assert exit_code == 2
        captured = capsys.readouterr()
        assert "DSN required" in captured.err

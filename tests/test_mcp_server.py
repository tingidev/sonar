"""Tests for the MCP server bootstrap and its startup failure modes.

Covers each scenario in the mcp-server spec's "Serve subcommand starts an
MCP server over a Sonar bundle" requirement and the "Sample tool is registered
only when a DSN is provided" requirement.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import sonar.cli
from sonar.cli import main
from sonar.connectors.types import Column, Table
from sonar.index.bundle import (
    BundleIntegrityError,
    BundleMeta,
    BundleVersionError,
    ContextBundle,
)
from sonar.index.store import ContextStore
from sonar.mcp.server import build_server


def _tiny_bundle() -> ContextBundle:
    users = Table(
        schema="public",
        name="users",
        columns=(Column("user_id", "uuid", nullable=False, is_primary_key=True),),
    )
    return ContextBundle(
        meta=BundleMeta(
            schema_version=1,
            generated_at="2026-04-23T00:00:00Z",
            connector="postgres",
            database="test",
        ),
        tables=(users,),
        descriptions={("public", "users"): None},
        relationships=(),
    )


def _registered_tool_names(app) -> set[str]:
    tools = asyncio.run(app.list_tools())
    return {t.name for t in tools}


class TestBundleOnlyMode:
    def test_tool_list_excludes_sample(self) -> None:
        app = build_server(_tiny_bundle(), dsn=None)
        names = _registered_tool_names(app)
        assert names == {"discover", "describe", "relationships", "search"}
        assert "sample" not in names


class TestLiveMode:
    def test_tool_list_includes_sample_plus_four(self) -> None:
        app = build_server(_tiny_bundle(), dsn="postgresql://sonar:pw@localhost:5432/db")
        names = _registered_tool_names(app)
        assert "sample" in names
        for expected in ("discover", "describe", "relationships", "search"):
            assert expected in names


class TestStartupFailures:
    def test_missing_bundle_dir_aborts_non_zero(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls: list[object] = []

        def _unreachable_build_server(*args: object, **kwargs: object) -> None:
            calls.append(args)
            raise AssertionError("build_server must not be called if bundle missing")

        monkeypatch.setattr(sonar.cli, "build_server", _unreachable_build_server)

        missing = tmp_path / "nonexistent"
        exit_code = main(["serve", "--bundle-dir", str(missing)])

        assert exit_code == 1
        captured = capsys.readouterr()
        assert "no bundle found" in captured.err.lower() or "serve failed" in captured.err.lower()
        assert calls == []

    def test_corrupt_bundle_aborts_non_zero(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls: list[object] = []

        def _unreachable_build_server(*args: object, **kwargs: object) -> None:
            calls.append(args)
            raise AssertionError("build_server must not be called on corrupt bundle")

        monkeypatch.setattr(sonar.cli, "build_server", _unreachable_build_server)

        def _raise_integrity(self: object) -> None:
            raise BundleIntegrityError("descriptions.json missing key 'public.orders'")

        monkeypatch.setattr(ContextStore, "read", _raise_integrity)

        exit_code = main(["serve", "--bundle-dir", str(tmp_path)])
        assert exit_code == 1
        captured = capsys.readouterr()
        assert "serve failed" in captured.err
        assert calls == []

    def test_version_mismatch_bundle_aborts_non_zero(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls: list[object] = []

        def _unreachable_build_server(*args: object, **kwargs: object) -> None:
            calls.append(args)
            raise AssertionError("build_server must not be called on version mismatch")

        monkeypatch.setattr(sonar.cli, "build_server", _unreachable_build_server)

        def _raise_version(self: object) -> None:
            raise BundleVersionError(expected=1, found=99)

        monkeypatch.setattr(ContextStore, "read", _raise_version)

        exit_code = main(["serve", "--bundle-dir", str(tmp_path)])
        assert exit_code == 1
        captured = capsys.readouterr()
        assert "serve failed" in captured.err
        assert calls == []

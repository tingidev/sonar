"""CLI-level tests for `sonar serve`.

Startup-failure scenarios for `_run_serve` live in `test_mcp_server.py` so they
sit next to the spec scenarios they directly assert. This file focuses on CLI
argument-parsing smoke checks.
"""

from __future__ import annotations

import pytest

from sonar.cli import main


class TestServeCli:
    def test_help_exits_cleanly(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as excinfo:
            main(["serve", "--help"])
        assert excinfo.value.code == 0
        captured = capsys.readouterr()
        assert "--bundle-dir" in captured.out
        assert "--allow-pii" in captured.out

    def test_missing_bundle_dir_exits_nonzero_with_clear_stderr(
        self,
        tmp_path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        missing = tmp_path / "does_not_exist"
        exit_code = main(["serve", "--bundle-dir", str(missing)])
        assert exit_code == 1
        captured = capsys.readouterr()
        # Error line must name the problem and mention the missing path.
        assert "no bundle found" in captured.err.lower()
        assert str(missing) in captured.err

    def test_corrupt_bundle_exits_nonzero_with_scrubbed_stderr(
        self,
        tmp_path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from sonar.index.bundle import BundleIntegrityError
        from sonar.index.store import ContextStore

        # Embed a DSN-looking substring in the error to verify stderr never
        # leaks credentials when the bundle itself is the failure source.
        secret_dsn = "postgresql://sonar:hunter2@host/db"

        def _raise_integrity(self: object) -> None:
            raise BundleIntegrityError(f"corrupt fragment referencing {secret_dsn}")

        monkeypatch.setattr(ContextStore, "read", _raise_integrity)

        exit_code = main(["serve", "--bundle-dir", str(tmp_path), secret_dsn])
        assert exit_code == 1
        captured = capsys.readouterr()
        assert "serve failed" in captured.err
        # DSN passed positionally should never re-appear in stderr.
        assert "hunter2" not in captured.err
        assert secret_dsn not in captured.err

    def test_corrupt_bundle_in_bundle_only_mode_does_not_crash_on_scrub(
        self,
        tmp_path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # Bundle-only mode: no DSN on the CLI. `scrub_dsn` is still called
        # unconditionally on the error path, so a `None` DSN must not break
        # rendering. The DSN-looking substring embedded in the exception is
        # not scrubbed (there's no DSN to scrub against in this mode), but
        # the process must still exit cleanly with a clear stderr.
        from sonar.index.bundle import BundleIntegrityError
        from sonar.index.store import ContextStore

        def _raise_integrity(self: object) -> None:
            raise BundleIntegrityError("corrupt bundle fragment")

        monkeypatch.setattr(ContextStore, "read", _raise_integrity)

        exit_code = main(["serve", "--bundle-dir", str(tmp_path)])
        assert exit_code == 1
        captured = capsys.readouterr()
        assert "serve failed" in captured.err
        assert "BundleIntegrityError" in captured.err

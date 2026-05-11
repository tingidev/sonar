"""Unit tests for `sonar.scan_output` — formatting only, no IO beyond stdout."""

from __future__ import annotations

from pathlib import Path

import pytest

from sonar.engine.describe import DescribeProgress
from sonar.index.bundle import BundleMeta, ContextBundle
from sonar.scan_output import (
    FailedTable,
    print_discovery,
    print_scan_summary,
    print_table_progress,
)


def _bundle(table_count: int = 0, relationship_count: int = 0) -> ContextBundle:
    from sonar.connectors.types import Column, Table

    tables = tuple(
        Table(
            schema="public",
            name=f"t{i}",
            columns=(Column(name="id", data_type="int", nullable=False, is_primary_key=True),),
        )
        for i in range(table_count)
    )
    relationships = tuple(object() for _ in range(relationship_count))  # type: ignore[misc]
    meta = BundleMeta(
        schema_version=1,
        generated_at="2026-01-01T00:00:00Z",
        connector="postgres",
        database="test",
    )
    return ContextBundle(
        meta=meta,
        tables=tables,
        descriptions={},
        relationships=relationships,  # type: ignore[arg-type]
    )


class TestPrintDiscovery:
    def test_renders_table_and_schema_counts(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        print_discovery(15, 3)
        out = capsys.readouterr().out
        assert "Discovered 15 tables in 3 schemas" in out

    def test_zero_counts_render(self, capsys: pytest.CaptureFixture[str]) -> None:
        print_discovery(0, 0)
        out = capsys.readouterr().out
        assert "0 tables" in out
        assert "0 schemas" in out


class TestPrintTableProgress:
    def test_started_event_omits_elapsed(self, capsys: pytest.CaptureFixture[str]) -> None:
        event = DescribeProgress(
            index=2, total=15, schema="public", table="orders", event="started"
        )
        print_table_progress(event)
        out = capsys.readouterr().out
        assert "[3/15] public.orders" in out
        assert "..." in out
        assert "ok" not in out
        assert "(0.0s)" not in out

    def test_ok_event_shows_elapsed_in_seconds(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        event = DescribeProgress(
            index=2,
            total=15,
            schema="public",
            table="orders",
            event="ok",
            elapsed_ms=2134,
        )
        print_table_progress(event)
        out = capsys.readouterr().out
        assert "[3/15] public.orders" in out
        assert "ok" in out
        assert "(2.1s)" in out

    def test_parse_retry_event_marked_as_retried(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        event = DescribeProgress(
            index=0,
            total=1,
            schema="public",
            table="users",
            event="parse_retry",
            elapsed_ms=3500,
        )
        print_table_progress(event)
        out = capsys.readouterr().out
        assert "[1/1] public.users" in out
        assert "ok via retry" in out
        assert "(3.5s)" in out

    def test_failed_event_includes_reason(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        event = DescribeProgress(
            index=4,
            total=15,
            schema="public",
            table="events",
            event="failed",
            elapsed_ms=4200,
            error_reason="parse error after retry",
        )
        print_table_progress(event)
        out = capsys.readouterr().out
        assert "[5/15] public.events" in out
        assert "failed: parse error after retry" in out
        assert "(4.2s)" in out

    def test_provider_error_renders_as_failed_with_reason(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        event = DescribeProgress(
            index=0,
            total=1,
            schema="public",
            table="audits",
            event="provider_error",
            elapsed_ms=12000,
            error_reason="rate limit exceeded",
        )
        print_table_progress(event)
        out = capsys.readouterr().out
        assert "failed: rate limit exceeded" in out
        assert "(12.0s)" in out

    def test_missing_reason_falls_back_to_unknown(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        event = DescribeProgress(
            index=0,
            total=1,
            schema="s",
            table="t",
            event="failed",
            elapsed_ms=500,
        )
        print_table_progress(event)
        out = capsys.readouterr().out
        assert "failed: unknown error" in out


class TestPrintScanSummary:
    def test_clean_summary_omits_failure_section(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        bundle = _bundle(table_count=15, relationship_count=8)
        print_scan_summary(
            database_label="prod-db",
            bundle=bundle,
            bundle_dir=Path(".sonar"),
            elapsed_seconds=34.2,
        )
        out = capsys.readouterr().out
        assert "34.2s" in out
        assert "prod-db" in out
        assert "15 tables" in out
        assert "8 relationships" in out
        assert ".sonar" in out
        assert "15 ok, 0 failed" in out
        assert "Failed tables:" not in out

    def test_partial_failure_lists_failed_tables(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        bundle = _bundle(table_count=15, relationship_count=2)
        failures = (
            FailedTable(schema="public", name="events", error_reason="rate limit exceeded"),
            FailedTable(schema="public", name="audits", error_reason="parse error after retry"),
        )
        print_scan_summary(
            database_label="prod-db",
            bundle=bundle,
            bundle_dir=Path(".sonar"),
            elapsed_seconds=42.0,
            failures=failures,
        )
        out = capsys.readouterr().out
        assert "13 ok, 2 failed" in out
        assert "Failed tables:" in out
        assert "public.events: rate limit exceeded" in out
        assert "public.audits: parse error after retry" in out
        assert "retry" in out.lower()

    def test_cross_database_warning_rendered(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        bundle = _bundle()
        print_scan_summary(
            database_label="snow",
            bundle=bundle,
            bundle_dir=Path(".sonar"),
            elapsed_seconds=1.0,
            cross_database_dropped=3,
            cross_database_label="TEST_DB",
        )
        out = capsys.readouterr().out
        assert "3 foreign keys reference tables outside database TEST_DB" in out

    def test_cross_database_warning_falls_back_to_label(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        bundle = _bundle()
        print_scan_summary(
            database_label="DB",
            bundle=bundle,
            bundle_dir=Path(".sonar"),
            elapsed_seconds=1.0,
            cross_database_dropped=2,
        )
        out = capsys.readouterr().out
        assert "2 foreign keys reference tables outside database DB" in out

    def test_cross_dataset_warning_rendered(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        bundle = _bundle()
        print_scan_summary(
            database_label="bq",
            bundle=bundle,
            bundle_dir=Path(".sonar"),
            elapsed_seconds=1.0,
            cross_dataset_dropped=4,
        )
        out = capsys.readouterr().out
        assert "4 foreign keys reference tables outside their dataset" in out

    def test_warnings_silent_when_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        bundle = _bundle(table_count=2)
        print_scan_summary(
            database_label="db",
            bundle=bundle,
            bundle_dir=Path(".sonar"),
            elapsed_seconds=1.0,
        )
        out = capsys.readouterr().out
        assert "foreign keys reference tables outside" not in out

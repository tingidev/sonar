"""Tests for the mcp sample audit logger."""

from __future__ import annotations

import logging

import pytest

from sonar.mcp.audit import emit_sample_audit


class TestEmitSampleAudit:
    def test_ok_record_contains_documented_fields(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="sonar.mcp.audit"):
            emit_sample_audit(
                outcome="ok",
                schema="public",
                table="users",
                limit_requested=10,
                limit_effective=10,
                rows_returned=7,
            )

        records = [r for r in caplog.records if r.name == "sonar.mcp.audit"]
        assert len(records) == 1
        rec = records[0]
        assert rec.tool == "sample"
        assert rec.outcome == "ok"
        assert rec.schema == "public"
        assert rec.table == "users"
        assert rec.limit_requested == 10
        assert rec.limit_effective == 10
        assert rec.rows_returned == 7

    def test_rejection_record_has_null_effective_and_rows(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="sonar.mcp.audit"):
            emit_sample_audit(
                outcome="rejected_cap",
                schema="public",
                table="users",
                limit_requested=1000,
                limit_effective=None,
                rows_returned=None,
            )

        records = [r for r in caplog.records if r.name == "sonar.mcp.audit"]
        assert len(records) == 1
        rec = records[0]
        assert rec.outcome == "rejected_cap"
        assert rec.limit_requested == 1000
        assert rec.limit_effective is None
        assert rec.rows_returned is None

    def test_record_excludes_credential_and_row_content_keys(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="sonar.mcp.audit"):
            emit_sample_audit(
                outcome="ok",
                schema="public",
                table="users",
                limit_requested=5,
                limit_effective=5,
                rows_returned=5,
            )

        rec = [r for r in caplog.records if r.name == "sonar.mcp.audit"][0]
        # The audit record must not carry credential or row-content fields.
        # Whitelist the keys we document; anything else on the record is a leak.
        expected_keys = {
            "tool",
            "outcome",
            "schema",
            "table",
            "limit_requested",
            "limit_effective",
            "rows_returned",
        }
        # LogRecord also has standard attrs (name, msg, levelname, etc.);
        # only custom extras would carry forbidden data. Sample those with
        # a negative check for known-bad names.
        forbidden = {
            "dsn",
            "password",
            "row",
            "rows",
            "values",
            "query",
            "connection_string",
        }
        attrs = vars(rec)
        for key in forbidden:
            assert key not in attrs, f"audit record leaked forbidden key {key!r}"
        for key in expected_keys:
            assert key in attrs, f"audit record missing documented key {key!r}"

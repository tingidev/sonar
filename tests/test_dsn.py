"""Unit tests for `sonar._dsn.scrub_dsn`."""

from __future__ import annotations

from sonar._dsn import scrub_dsn
from sonar.index.bundle import format_database_label


class TestScrubDsn:
    def test_substring_present_is_replaced_with_label(self) -> None:
        dsn = "postgresql://sonar:hunter2@localhost:5432/sonar_test"
        message = f"OperationalError: connection failed to {dsn}"

        scrubbed = scrub_dsn(message, dsn)

        assert "hunter2" not in scrubbed
        assert dsn not in scrubbed
        assert format_database_label(dsn) in scrubbed

    def test_substring_absent_returns_message_unchanged(self) -> None:
        dsn = "postgresql://sonar:hunter2@localhost:5432/sonar_test"
        message = "Some error that happened to not include the DSN"

        assert scrub_dsn(message, dsn) == message

    def test_password_with_regex_specials_is_handled(self) -> None:
        # Passwords occasionally contain characters that would be regex
        # metacharacters under re.sub. scrub_dsn uses str.replace, so these
        # pass through as literal substrings.
        dsn = "postgresql://user:a+b(c)?.*@host:5432/db"
        message = f"auth failed at {dsn} (check the string)"

        scrubbed = scrub_dsn(message, dsn)

        assert dsn not in scrubbed
        assert "a+b(c)?.*" not in scrubbed
        assert format_database_label(dsn) in scrubbed

    def test_empty_dsn_returns_message_unchanged(self) -> None:
        message = "some error text"
        assert scrub_dsn(message, "") == message

    def test_none_dsn_returns_message_unchanged(self) -> None:
        # Callers on error paths may not have a DSN to scrub against (e.g.
        # `sonar serve` in bundle-only mode). The helper must accept None so
        # those callers can invoke it unconditionally.
        message = "some error text"
        assert scrub_dsn(message, None) == message

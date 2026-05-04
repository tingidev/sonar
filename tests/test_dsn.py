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

    def test_password_substring_alone_is_scrubbed(self) -> None:
        # snowflake-connector-python sometimes embeds just the password in
        # exception messages (without the surrounding URL). Postgres' psycopg
        # exhibits this less frequently. Either way, the parsed password must
        # be replaced with the placeholder.
        dsn = "snowflake://alice:s3cr3t@acct.eu-west-1/DB/SCHEMA"
        message = "DatabaseError: password 's3cr3t' is incorrect for user 'alice'"

        scrubbed = scrub_dsn(message, dsn)

        assert "s3cr3t" not in scrubbed
        assert "***" in scrubbed
        assert "alice" in scrubbed

    def test_url_encoded_password_is_scrubbed_in_both_forms(self) -> None:
        # If the password contains URL-reserved characters, the user supplies
        # them percent-encoded, but the driver receives (and may quote) the
        # decoded form. Both must be stripped.
        dsn = "snowflake://alice:s%40cr3t@acct/DB/SCHEMA"
        message_decoded = "auth failed: 's@cr3t' rejected"
        message_raw = "internal: token=s%40cr3t"

        assert "s@cr3t" not in scrub_dsn(message_decoded, dsn)
        assert "s%40cr3t" not in scrub_dsn(message_raw, dsn)

    def test_bare_snowflake_keyword_does_not_mangle_driver_errors(self) -> None:
        # The bare-keyword form carries no credentials. scrub_dsn must not
        # replace "snowflake" substrings in driver class names like
        # "snowflake.connector.errors.DatabaseError".
        message = "snowflake.connector.errors.DatabaseError: connection refused"
        assert scrub_dsn(message, "snowflake") == message

    def test_dsn_without_password_does_not_crash(self) -> None:
        # A URL with no password component (e.g. external-browser auth) has
        # `parsed.password is None`; the second pass must short-circuit.
        dsn = "snowflake://alice@acct/DB/SCHEMA"
        message = f"connection failed at {dsn}"

        scrubbed = scrub_dsn(message, dsn)

        assert dsn not in scrubbed
        assert "alice" in scrubbed

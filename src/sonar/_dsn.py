"""DSN credential scrubbing — shared between scan and serve error paths.

Replaces any occurrence of a raw DSN in a message with the password-stripped
label produced by `format_database_label`. Used wherever an error message may
embed the full connection string (psycopg's `OperationalError` is the first
concrete case; the MCP `sample` tool is the second).
"""

from __future__ import annotations

from sonar.index.bundle import format_database_label


def scrub_dsn(message: str, dsn: str | None) -> str:
    """Return `message` with every substring equal to `dsn` replaced by its label.

    If `dsn` is `None`, an empty string, or does not appear in `message`, the
    message is returned unchanged. Accepting `None` lets callers invoke the
    helper unconditionally on error paths regardless of whether a DSN was ever
    present (e.g. `sonar serve` in bundle-only mode). The replacement uses plain
    string substitution, so DSNs containing regex metacharacters (e.g. `+`, `(`,
    `)`, `?`) are handled safely.
    """
    if not dsn:
        return message
    return message.replace(dsn, format_database_label(dsn))

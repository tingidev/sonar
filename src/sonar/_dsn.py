"""DSN credential scrubbing — shared between scan and serve error paths.

Replaces any occurrence of a raw DSN in a message with the password-stripped
label produced by `format_database_label`, then strips the parsed password
substring on its own. Used wherever an error message may embed the full
connection string OR a free-standing password (psycopg embeds the full DSN;
snowflake-connector-python sometimes quotes only the password).
"""

from __future__ import annotations

from urllib.parse import unquote, urlparse

from sonar.index.bundle import format_database_label

_PASSWORD_PLACEHOLDER = "***"


def scrub_dsn(message: str, dsn: str | None) -> str:
    """Return `message` with `dsn` and its parsed password stripped.

    Two passes: (1) replace every substring equal to `dsn` with its label;
    (2) replace every occurrence of the parsed password with `***`. The second
    pass catches driver exceptions that quote only the password without the
    surrounding URL.

    If `dsn` is `None`, an empty string, or has no password component, only
    the first pass runs (and is a no-op when the DSN is missing). Accepting
    `None` lets callers invoke the helper unconditionally on error paths
    (e.g. `sonar serve` in bundle-only mode, or the bare-keyword `snowflake`
    positional which carries no credentials at all).
    """
    if not dsn:
        return message
    # Bare keywords (e.g. "snowflake") carry no credentials — skip the
    # full-string replacement pass which would mangle driver class names
    # like "snowflake.connector.errors.DatabaseError" into "unknown...".
    if "://" in dsn:
        message = message.replace(dsn, format_database_label(dsn))
    try:
        password = urlparse(dsn).password
    except ValueError:
        return message
    if not password:
        return message
    decoded = unquote(password)
    message = message.replace(decoded, _PASSWORD_PLACEHOLDER)
    if decoded != password:
        message = message.replace(password, _PASSWORD_PLACEHOLDER)
    return message

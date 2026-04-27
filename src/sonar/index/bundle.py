"""ContextBundle - typed composition of the three capability outputs."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from sonar.connectors.postgres import Table
from sonar.engine.describe import TableDescription
from sonar.relationships import Relationship

SCHEMA_VERSION: int = 1


class BundleVersionError(Exception):
    """Raised when a persisted bundle declares a schema version this code does not support."""

    def __init__(self, *, expected: int, found: int) -> None:
        super().__init__(f"Bundle schema version mismatch: expected {expected}, found {found}")
        self.expected = expected
        self.found = found


class BundleIntegrityError(Exception):
    """Raised when the bundle's tables and descriptions files disagree on keys."""


@dataclass(frozen=True)
class BundleMeta:
    schema_version: int
    generated_at: str
    connector: str
    database: str


@dataclass(frozen=True)
class ContextBundle:
    meta: BundleMeta
    tables: tuple[Table, ...]
    descriptions: dict[tuple[str, str], TableDescription | None]
    relationships: tuple[Relationship, ...]


def format_database_label(dsn: str) -> str:
    """Extract `[user@]host[:port][/dbname]` from a psycopg DSN; strip any password.

    Falls back to the literal string `"unknown"` on unparseable input so the
    resulting `BundleMeta.database` field stays printable and contains no
    credentials. Intended purely for operator-facing display; never parsed.
    """
    if not dsn:
        return "unknown"
    try:
        parsed = urlparse(dsn)
        if not parsed.scheme or not parsed.hostname:
            return "unknown"
        parts: list[str] = []
        if parsed.username:
            parts.append(f"{parsed.username}@")
        parts.append(parsed.hostname)
        if parsed.port:
            parts.append(f":{parsed.port}")
        if parsed.path and parsed.path != "/":
            parts.append(parsed.path)
        return "".join(parts)
    except ValueError:
        return "unknown"

"""Shared types for the eval subsystem."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RelationshipEdge:
    source_schema: str
    source_table: str
    source_column: str
    target_schema: str
    target_table: str
    target_column: str
    kind: str | None = None

"""Shared connector dataclasses — emitted by every connector implementation."""

from dataclasses import dataclass


def _reject_dotted_identifier(kind: str, value: str) -> None:
    if "." in value:
        raise ValueError(
            f"{kind} identifier contains '.': {value!r}. "
            "Sonar requires identifiers without '.' so the context-index bundle's "
            "on-disk key encoding stays unambiguous."
        )


@dataclass(frozen=True)
class Column:
    name: str
    data_type: str
    nullable: bool
    is_primary_key: bool
    default: str | None = None


@dataclass(frozen=True)
class Table:
    schema: str
    name: str
    columns: tuple[Column, ...]
    row_count: int | None = None


@dataclass(frozen=True)
class ForeignKey:
    source_schema: str
    source_table: str
    source_column: str
    target_schema: str
    target_table: str
    target_column: str

"""Postgres connector — schema discovery and data sampling."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Column:
    name: str
    data_type: str
    nullable: bool
    is_primary_key: bool
    foreign_key: str | None = None
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


class PostgresConnector:
    def __init__(self, connection_string: str):
        self._connection_string = connection_string

    async def discover_tables(self, schemas: list[str] | None = None) -> list[Table]:
        raise NotImplementedError

    async def discover_relationships(self) -> list[ForeignKey]:
        raise NotImplementedError

    async def sample_table(self, schema: str, table: str, limit: int = 5) -> list[dict]:
        raise NotImplementedError

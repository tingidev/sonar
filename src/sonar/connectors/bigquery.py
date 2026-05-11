"""BigQuery connector — schema discovery and data sampling.

Async via `asyncio.to_thread` against the sync `google-cloud-bigquery` client
(per design.md D2). Schema discovery uses the REST API (`list_datasets`,
`list_tables`, `get_table`) rather than INFORMATION_SCHEMA, because the latter
is region-scoped and requires a separate API call per dataset to determine its
region (per design.md D1). FK/PK constraints are still fetched via per-dataset
INFORMATION_SCHEMA (per design.md D5); the REST API has no FK endpoint.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

# Module assumes cli._ensure_bigquery_driver() ran before this file is imported.
# Direct imports from elsewhere bypass the guard and get a raw ModuleNotFoundError.
from google.cloud import bigquery

from sonar.connectors import _bigquery_sql as _bq_sql
from sonar.connectors.serialize import _serialize_row
from sonar.connectors.types import Column, ForeignKey, Table, _reject_dotted_identifier

_LOGGER = logging.getLogger("sonar.connectors.bigquery")
_CONTEXT_MANAGER_REQUIRED = "BigQueryConnector must be used as an async context manager"

# Bounds parallelism for per-table `get_table` calls. 20 covers the common case
# (hundreds of tables) without hitting BigQuery `tables.get` quota (per design.md D2).
_GET_TABLE_CONCURRENCY = 20


def _render_bq_type(field: bigquery.SchemaField) -> str:
    """Render a BigQuery type as a Sonar `data_type` string.

    Flat types pass through unchanged (`STRING`, `INTEGER`, ...). RECORD/STRUCT
    types render as `RECORD<name TYPE, ...>` recursively. Fields with mode
    `REPEATED` append ` REPEATED` to the type string (per design.md D3).
    """
    if field.field_type in ("RECORD", "STRUCT"):
        inner = ", ".join(f"{sub.name} {_render_bq_type(sub)}" for sub in field.fields)
        base = f"RECORD<{inner}>"
    else:
        base = field.field_type
    if field.mode == "REPEATED":
        return f"{base} REPEATED"
    return base


def _column_from_schema_field(field: bigquery.SchemaField, pk_columns: set[str]) -> Column:
    return Column(
        name=field.name,
        data_type=_render_bq_type(field),
        nullable=(field.mode != "REQUIRED"),
        is_primary_key=(field.name in pk_columns),
        default=None,
    )


class BigQueryConnector:
    """BigQuery schema discovery + sampling. Same observable contract as PostgresConnector."""

    def __init__(self, project_id: str, dataset_id: str | None = None) -> None:
        self._project_id = project_id
        self._dataset_id = dataset_id
        self._client: Any | None = None
        self.cross_dataset_foreign_keys_dropped: int = 0

    @property
    def project_id(self) -> str:
        return self._project_id

    @property
    def dataset_id(self) -> str | None:
        return self._dataset_id

    async def __aenter__(self) -> BigQueryConnector:
        self._client = await asyncio.to_thread(bigquery.Client, project=self._project_id)
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._client is not None:
            await asyncio.to_thread(self._client.close)
            self._client = None

    async def discover_tables(self, schemas: list[str] | None = None) -> list[Table]:
        if self._client is None:
            raise RuntimeError(_CONTEXT_MANAGER_REQUIRED)

        datasets = await self._resolve_datasets(schemas)
        if not datasets:
            return []

        # Constraint info per dataset, indexed by (dataset, table) for fast PK lookup.
        # Isolate per-dataset failures: a single restricted dataset must not prevent
        # discovery of the rest (mirrors discover_relationships per spec).
        pk_index: dict[tuple[str, str], set[str]] = {}
        for dataset_id in datasets:
            try:
                pks, _fks = await self._fetch_constraints(dataset_id)
            except Exception as exc:  # noqa: BLE001 - per-dataset isolation
                _LOGGER.warning(
                    "constraint discovery failed for dataset %r: %s; "
                    "proceeding without PK info for this dataset",
                    dataset_id,
                    exc,
                )
                continue
            for table_name, column_name in pks:
                pk_index.setdefault((dataset_id, table_name), set()).add(column_name)

        # Enumerate tables per dataset.
        sem = asyncio.Semaphore(_GET_TABLE_CONCURRENCY)
        coros: list[Any] = []
        for dataset_id in datasets:
            table_refs = await asyncio.to_thread(self._list_tables, dataset_id)
            for table_ref in table_refs:
                pks = pk_index.get((dataset_id, table_ref.table_id), set())
                coros.append(self._discover_table(sem, dataset_id, table_ref, pks))

        if not coros:
            return []
        results: list[Table] = await asyncio.gather(*coros)
        return results

    async def discover_relationships(self) -> list[ForeignKey]:
        if self._client is None:
            raise RuntimeError(_CONTEXT_MANAGER_REQUIRED)

        # Relationships are discovered across all datasets in the project, not
        # scoped to the datasets used in discover_tables. This matches the other
        # connectors where FK discovery is database-wide. Passing None to
        # _resolve_datasets unconditionally enumerates all datasets.
        datasets = await self._resolve_datasets(None)
        result: list[ForeignKey] = []
        dropped = 0
        for dataset_id in datasets:
            try:
                _pks, fk_rows = await self._fetch_constraints(dataset_id)
            except Exception as exc:  # noqa: BLE001 - per-dataset isolation
                _LOGGER.warning(
                    "constraint discovery failed for dataset %r: %s; skipping",
                    dataset_id,
                    exc,
                )
                continue
            for row in fk_rows:
                target_schema = row.get("target_schema")
                target_table = row.get("target_table")
                target_column = row.get("target_column")
                if target_schema is None or target_table is None or target_column is None:
                    # Constraint row missing target columns — defensive skip.
                    continue
                if target_schema != dataset_id:
                    dropped += 1
                    continue
                source_table = row["source_table"]
                source_column = row["source_column"]

                _reject_dotted_identifier("source schema", dataset_id)
                _reject_dotted_identifier("source table", source_table)
                _reject_dotted_identifier("source column", source_column)
                _reject_dotted_identifier("target schema", target_schema)
                _reject_dotted_identifier("target table", target_table)
                _reject_dotted_identifier("target column", target_column)

                result.append(
                    ForeignKey(
                        source_schema=dataset_id,
                        source_table=source_table,
                        source_column=source_column,
                        target_schema=target_schema,
                        target_table=target_table,
                        target_column=target_column,
                    )
                )

        self.cross_dataset_foreign_keys_dropped = dropped
        if dropped:
            _LOGGER.warning(
                "%d foreign key column(s) reference tables outside their dataset and were excluded",
                dropped,
            )
        return result

    async def sample_table(self, schema: str, table: str, limit: int = 5) -> list[dict]:
        if self._client is None:
            raise RuntimeError(_CONTEXT_MANAGER_REQUIRED)
        if not isinstance(limit, int) or limit < 0:
            raise ValueError(f"limit must be a non-negative int, got {limit!r}")

        _reject_dotted_identifier("schema", schema)
        _reject_dotted_identifier("table", table)
        # Project IDs can contain hyphens (common) and colons (domain-scoped
        # projects, e.g. `example.com:my-project`); quote them as well. The
        # project ID itself skips _reject_dotted_identifier because domain-scoped
        # IDs legitimately contain a dot — backtick quoting handles it safely.
        query = (
            f"SELECT * FROM {_bq_quote(self._project_id)}."
            f"{_bq_quote(schema)}.{_bq_quote(table)} "
            f"LIMIT {int(limit)}"
        )
        rows = await asyncio.to_thread(self._run_query_as_dicts, query)
        return [_serialize_row(row) for row in rows]

    async def _resolve_datasets(self, schemas: list[str] | None) -> list[str]:
        if schemas is not None:
            return schemas
        if self._dataset_id is not None:
            return [self._dataset_id]
        return await self._list_all_datasets()

    async def _list_all_datasets(self) -> list[str]:
        items = await asyncio.to_thread(self._list_datasets_sync)
        return items

    def _list_datasets_sync(self) -> list[str]:
        assert self._client is not None
        return [ds.dataset_id for ds in self._client.list_datasets(project=self._project_id)]

    def _list_tables(self, dataset_id: str) -> list[Any]:
        assert self._client is not None
        ref = bigquery.DatasetReference(self._project_id, dataset_id)
        return list(self._client.list_tables(ref))

    async def _discover_table(
        self,
        sem: asyncio.Semaphore,
        dataset_id: str,
        table_ref: Any,
        pk_columns: set[str],
    ) -> Table:
        async with sem:
            table = await asyncio.to_thread(self._client.get_table, table_ref)
        _reject_dotted_identifier("schema", dataset_id)
        _reject_dotted_identifier("table", table.table_id)
        columns = tuple(_column_from_schema_field(f, pk_columns) for f in table.schema)
        row_count = int(table.num_rows) if table.num_rows is not None else None
        return Table(
            schema=dataset_id,
            name=table.table_id,
            columns=columns,
            row_count=row_count,
        )

    async def _fetch_constraints(self, dataset_id: str) -> tuple[set[tuple[str, str]], list[dict]]:
        """Return (pk_set, fk_rows) for one dataset.

        `pk_set` is a set of `(table_name, column_name)` pairs.
        `fk_rows` is a list of dicts with keys: source_table, source_column,
        target_schema, target_table, target_column.
        """
        query = _bq_sql.constraints_query(self._project_id, dataset_id)
        rows = await asyncio.to_thread(self._run_query_as_dicts, query)
        pk_set: set[tuple[str, str]] = set()
        fk_rows: list[dict] = []
        for row in rows:
            if row.get("constraint_type") == "PRIMARY KEY":
                pk_set.add((row["source_table"], row["source_column"]))
            elif row.get("constraint_type") == "FOREIGN KEY":
                fk_rows.append(row)
        return pk_set, fk_rows

    def _run_query_as_dicts(self, query: str) -> list[dict]:
        assert self._client is not None
        job = self._client.query(query)
        result = job.result()
        # Each Row is a named tuple, not a mapping for serialize purposes;
        # convert explicitly (per design.md D8).
        return [dict(row) for row in result]


def _bq_quote(name: str) -> str:
    """BigQuery identifier quoting: wrap in backticks, escape internal backticks.

    Cross-reference: keep in sync with `_backtick` in connectors/_bigquery_sql.py.
    """
    if "\x00" in name:
        raise ValueError(f"identifier contains null byte: {name!r}")
    escaped = name.replace("`", "\\`")
    return f"`{escaped}`"

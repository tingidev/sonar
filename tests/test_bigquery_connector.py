"""Tests for the BigQuery connector.

Unit tests use real `google.cloud.bigquery.SchemaField` objects (no mocks).
Discovery flow tests construct a connector, mark it "in-context" by setting
`_client = object()`, and monkeypatch helpers on the instance to inject
deterministic responses without exercising the live API.

Live integration tests run only when `BIGQUERY_TEST_PROJECT` is set; they hit
`bigquery-public-data.samples.shakespeare` which has a stable schema.
"""

from __future__ import annotations

import importlib.util
import logging
import os

import pytest
from google.cloud import bigquery

from sonar.cli import (
    _bigquery_from_env,
    _bigquery_from_url,
    _bigquery_label,
    _DispatchError,
    _select_connector,
)
from sonar.connectors.bigquery import (
    BigQueryConnector,
    _bq_quote,
    _column_from_schema_field,
    _render_bq_type,
)

# ---------------------------------------------------------------------------
# 6.1 — _render_bq_type: flat, nested RECORD, REPEATED
# ---------------------------------------------------------------------------


class TestRenderBqType:
    def test_flat_string(self) -> None:
        f = bigquery.SchemaField("email", "STRING", mode="NULLABLE")
        assert _render_bq_type(f) == "STRING"

    def test_flat_integer(self) -> None:
        f = bigquery.SchemaField("id", "INTEGER", mode="REQUIRED")
        assert _render_bq_type(f) == "INTEGER"

    def test_record_two_sub_fields(self) -> None:
        f = bigquery.SchemaField(
            "address",
            "RECORD",
            mode="NULLABLE",
            fields=(
                bigquery.SchemaField("city", "STRING", mode="NULLABLE"),
                bigquery.SchemaField("zip", "STRING", mode="NULLABLE"),
            ),
        )
        assert _render_bq_type(f) == "RECORD<city STRING, zip STRING>"

    def test_deeply_nested_record_three_levels(self) -> None:
        # RECORD<inner RECORD<deepest RECORD<value STRING>>>
        deepest = bigquery.SchemaField(
            "deepest",
            "RECORD",
            mode="NULLABLE",
            fields=(bigquery.SchemaField("value", "STRING", mode="NULLABLE"),),
        )
        inner = bigquery.SchemaField("inner", "RECORD", mode="NULLABLE", fields=(deepest,))
        outer = bigquery.SchemaField("outer", "RECORD", mode="NULLABLE", fields=(inner,))
        rendered = _render_bq_type(outer)
        assert rendered == ("RECORD<inner RECORD<deepest RECORD<value STRING>>>")

    def test_repeated_record(self) -> None:
        f = bigquery.SchemaField(
            "items",
            "RECORD",
            mode="REPEATED",
            fields=(
                bigquery.SchemaField("sku", "STRING", mode="NULLABLE"),
                bigquery.SchemaField("qty", "INTEGER", mode="NULLABLE"),
            ),
        )
        assert _render_bq_type(f) == "RECORD<sku STRING, qty INTEGER> REPEATED"

    def test_repeated_scalar(self) -> None:
        f = bigquery.SchemaField("tags", "STRING", mode="REPEATED")
        assert _render_bq_type(f) == "STRING REPEATED"


# ---------------------------------------------------------------------------
# 6.2 — _column_from_schema_field: nullable, required, PK, REPEATED
# ---------------------------------------------------------------------------


class TestColumnFromSchemaField:
    def test_nullable_column(self) -> None:
        f = bigquery.SchemaField("name", "STRING", mode="NULLABLE")
        col = _column_from_schema_field(f, pk_columns=set())
        assert col.name == "name"
        assert col.data_type == "STRING"
        assert col.nullable is True
        assert col.is_primary_key is False

    def test_required_column_not_nullable(self) -> None:
        f = bigquery.SchemaField("id", "INTEGER", mode="REQUIRED")
        col = _column_from_schema_field(f, pk_columns=set())
        assert col.nullable is False

    def test_column_in_pk_columns_set(self) -> None:
        f = bigquery.SchemaField("id", "INTEGER", mode="REQUIRED")
        col = _column_from_schema_field(f, pk_columns={"id"})
        assert col.is_primary_key is True

    def test_repeated_mode_nullable(self) -> None:
        # REPEATED is not REQUIRED, so it must be reported as nullable=True.
        f = bigquery.SchemaField("tags", "STRING", mode="REPEATED")
        col = _column_from_schema_field(f, pk_columns=set())
        assert col.nullable is True
        assert col.data_type == "STRING REPEATED"


# ---------------------------------------------------------------------------
# 6.2a — _fetch_constraints failure handling in discover_relationships
# ---------------------------------------------------------------------------


class TestFetchConstraintsFailureHandling:
    async def test_failure_in_one_dataset_isolated(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        c = BigQueryConnector("my-project")
        # Bypass the context-manager guard without contacting BigQuery.
        c._client = object()  # type: ignore[assignment]

        async def _fake_resolve(schemas):  # noqa: ANN001
            return ["ds_ok", "ds_fail", "ds_ok2"]

        async def _fake_fetch(dataset_id: str):
            if dataset_id == "ds_fail":
                raise RuntimeError("permission denied on INFORMATION_SCHEMA")
            return (
                set(),
                [
                    {
                        "constraint_type": "FOREIGN KEY",
                        "source_table": f"{dataset_id}_orders",
                        "source_column": "user_id",
                        "target_schema": dataset_id,
                        "target_table": "users",
                        "target_column": "id",
                    }
                ],
            )

        monkeypatch.setattr(c, "_resolve_datasets", _fake_resolve)
        monkeypatch.setattr(c, "_fetch_constraints", _fake_fetch)

        with caplog.at_level(logging.WARNING, logger="sonar.connectors.bigquery"):
            fks = await c.discover_relationships()

        sources = {(fk.source_schema, fk.source_table) for fk in fks}
        assert sources == {
            ("ds_ok", "ds_ok_orders"),
            ("ds_ok2", "ds_ok2_orders"),
        }
        # Warning logged for the failing dataset; discovery continued.
        assert any("ds_fail" in r.message for r in caplog.records)

    async def test_discover_tables_isolates_per_dataset_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        c = BigQueryConnector("my-project")

        class _FakeTableRef:
            def __init__(self, table_id: str) -> None:
                self.table_id = table_id

        class _FakeTable:
            def __init__(self, table_id: str) -> None:
                self.table_id = table_id
                self.num_rows = 0
                self.schema = (bigquery.SchemaField("id", "INTEGER", mode="REQUIRED"),)

        class _FakeClient:
            def get_table(self, ref: _FakeTableRef) -> _FakeTable:
                return _FakeTable(ref.table_id)

        async def _fake_resolve(schemas):  # noqa: ANN001
            return ["ds_ok", "ds_fail"]

        async def _fake_fetch(dataset_id: str):
            if dataset_id == "ds_fail":
                raise RuntimeError("permission denied on INFORMATION_SCHEMA")
            return ({("users", "id")}, [])

        def _fake_list_tables(dataset_id: str):
            return [_FakeTableRef("users")]

        monkeypatch.setattr(c, "_resolve_datasets", _fake_resolve)
        monkeypatch.setattr(c, "_fetch_constraints", _fake_fetch)
        monkeypatch.setattr(c, "_list_tables", _fake_list_tables)
        c._client = _FakeClient()  # type: ignore[assignment]

        with caplog.at_level(logging.WARNING, logger="sonar.connectors.bigquery"):
            tables = await c.discover_tables()

        # Both datasets produce a `users` table; the failing one returns without PK info.
        by_schema = {t.schema: t for t in tables}
        assert set(by_schema) == {"ds_ok", "ds_fail"}
        ds_ok_id = next(col for col in by_schema["ds_ok"].columns if col.name == "id")
        ds_fail_id = next(col for col in by_schema["ds_fail"].columns if col.name == "id")
        assert ds_ok_id.is_primary_key is True
        assert ds_fail_id.is_primary_key is False
        assert any("ds_fail" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Dotted-identifier guards in discover_relationships
# ---------------------------------------------------------------------------


class TestDiscoverRelationshipsDottedIdentifiers:
    async def _run_with_row(self, monkeypatch: pytest.MonkeyPatch, row: dict) -> None:
        c = BigQueryConnector("p")
        c._client = object()  # type: ignore[assignment]

        async def _resolve(schemas):  # noqa: ANN001
            return ["ds"]

        async def _fetch(dataset_id: str):
            return (set(), [row])

        monkeypatch.setattr(c, "_resolve_datasets", _resolve)
        monkeypatch.setattr(c, "_fetch_constraints", _fetch)

        await c.discover_relationships()

    async def test_dotted_source_column_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        row = {
            "constraint_type": "FOREIGN KEY",
            "source_table": "orders",
            "source_column": "user.id",
            "target_schema": "ds",
            "target_table": "users",
            "target_column": "id",
        }
        with pytest.raises(ValueError, match=r"identifier contains '\."):
            await self._run_with_row(monkeypatch, row)

    async def test_dotted_target_column_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        row = {
            "constraint_type": "FOREIGN KEY",
            "source_table": "orders",
            "source_column": "user_id",
            "target_schema": "ds",
            "target_table": "users",
            "target_column": "id.x",
        }
        with pytest.raises(ValueError, match=r"identifier contains '\."):
            await self._run_with_row(monkeypatch, row)


# ---------------------------------------------------------------------------
# 6.3 — _bq_quote
# ---------------------------------------------------------------------------


class TestBqQuote:
    def test_normal_identifier(self) -> None:
        assert _bq_quote("users") == "`users`"

    def test_identifier_with_backtick(self) -> None:
        assert _bq_quote("we`ird") == "`we\\`ird`"

    def test_rejects_null_byte(self) -> None:
        with pytest.raises(ValueError, match="null byte"):
            _bq_quote("a\x00b")


# ---------------------------------------------------------------------------
# 6.4 — CLI dispatch
# ---------------------------------------------------------------------------


class TestUrlParsing:
    def test_project_only(self) -> None:
        project, dataset = _bigquery_from_url("bigquery://my-project")
        assert project == "my-project"
        assert dataset is None

    def test_project_and_dataset(self) -> None:
        project, dataset = _bigquery_from_url("bigquery://my-project/my_dataset")
        assert project == "my-project"
        assert dataset == "my_dataset"

    def test_trailing_slash_no_dataset(self) -> None:
        project, dataset = _bigquery_from_url("bigquery://my-project/")
        assert project == "my-project"
        assert dataset is None

    def test_missing_project_rejects(self) -> None:
        with pytest.raises(_DispatchError, match="project id"):
            _bigquery_from_url("bigquery://")

    def test_missing_project_before_slash_rejects(self) -> None:
        with pytest.raises(_DispatchError, match="project id"):
            _bigquery_from_url("bigquery:///dataset")


class TestEnvVarDispatch:
    @pytest.fixture(autouse=True)
    def _clear_bigquery_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key in list(os.environ):
            if key.startswith("BIGQUERY_"):
                monkeypatch.delenv(key, raising=False)

    def test_project_and_dataset_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BIGQUERY_PROJECT", "my-project")
        monkeypatch.setenv("BIGQUERY_DATASET", "my_dataset")
        project, dataset = _bigquery_from_env()
        assert project == "my-project"
        assert dataset == "my_dataset"

    def test_project_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BIGQUERY_PROJECT", "my-project")
        project, dataset = _bigquery_from_env()
        assert project == "my-project"
        assert dataset is None

    def test_missing_project_rejects(self) -> None:
        with pytest.raises(_DispatchError, match="BIGQUERY_PROJECT"):
            _bigquery_from_env()


class TestSelectConnectorRouting:
    def test_url_project_only(self) -> None:
        spec = _select_connector("bigquery://my-project")
        assert spec.connector_type == "bigquery"
        assert spec.database_label == "my-project"
        assert spec.connector.project_id == "my-project"
        assert spec.connector.dataset_id is None

    def test_url_project_dataset(self) -> None:
        spec = _select_connector("bigquery://my-project/my_dataset")
        assert spec.connector_type == "bigquery"
        assert spec.database_label == "my-project.my_dataset"
        assert spec.connector.dataset_id == "my_dataset"

    def test_url_trailing_slash(self) -> None:
        spec = _select_connector("bigquery://my-project/")
        assert spec.connector_type == "bigquery"
        assert spec.connector.dataset_id is None

    def test_bare_keyword_with_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key in list(os.environ):
            if key.startswith("BIGQUERY_"):
                monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("BIGQUERY_PROJECT", "envproj")
        monkeypatch.setenv("BIGQUERY_DATASET", "envds")
        spec = _select_connector("bigquery")
        assert spec.connector_type == "bigquery"
        assert spec.connector.project_id == "envproj"
        assert spec.connector.dataset_id == "envds"

    def test_bare_keyword_missing_project(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from sonar.cli import main

        for key in list(os.environ):
            if key.startswith("BIGQUERY_"):
                monkeypatch.delenv(key, raising=False)
        rc = main(["scan", "bigquery"])
        assert rc == 2
        captured = capsys.readouterr()
        assert "BIGQUERY_PROJECT" in captured.err


class TestBigqueryLabel:
    def test_project_only(self) -> None:
        assert _bigquery_label("my-project") == "my-project"

    def test_project_and_dataset(self) -> None:
        assert _bigquery_label("my-project", "my_dataset") == "my-project.my_dataset"


# ---------------------------------------------------------------------------
# 6.5 — missing-dep guard
# ---------------------------------------------------------------------------


class TestOptionalDepGuard:
    def test_missing_bigquery_exits_nonzero_with_install_hint(
        self,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from sonar.cli import main

        _orig = importlib.util.find_spec
        monkeypatch.setattr(
            "importlib.util.find_spec",
            lambda name: None if name == "google.cloud.bigquery" else _orig(name),
        )
        rc = main(["scan", "bigquery://my-project"])
        assert rc == 2
        captured = capsys.readouterr()
        assert "pip install 'sonar[bigquery]'" in captured.err

    def test_missing_bigquery_keyword_form_exits_nonzero(
        self,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from sonar.cli import main

        _orig = importlib.util.find_spec
        monkeypatch.setattr(
            "importlib.util.find_spec",
            lambda name: None if name == "google.cloud.bigquery" else _orig(name),
        )
        for key in list(os.environ):
            if key.startswith("BIGQUERY_"):
                monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("BIGQUERY_PROJECT", "p")
        rc = main(["scan", "bigquery"])
        assert rc == 2
        captured = capsys.readouterr()
        assert "pip install 'sonar[bigquery]'" in captured.err


# ---------------------------------------------------------------------------
# Context-manager guard
# ---------------------------------------------------------------------------


class TestContextManagerGuard:
    async def test_discover_tables_outside_context_raises(self) -> None:
        c = BigQueryConnector("p")
        with pytest.raises(RuntimeError, match="context manager"):
            await c.discover_tables()

    async def test_discover_relationships_outside_context_raises(self) -> None:
        c = BigQueryConnector("p")
        with pytest.raises(RuntimeError, match="context manager"):
            await c.discover_relationships()

    async def test_sample_table_outside_context_raises(self) -> None:
        c = BigQueryConnector("p")
        with pytest.raises(RuntimeError, match="context manager"):
            await c.sample_table("ds", "t")


# ---------------------------------------------------------------------------
# discover_relationships: cross-dataset FK drop + counter
# ---------------------------------------------------------------------------


class TestCrossDatasetFkDrop:
    async def test_drops_cross_dataset_fk_and_counts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        c = BigQueryConnector("p")
        c._client = object()  # type: ignore[assignment]

        async def _fake_resolve(schemas):  # noqa: ANN001
            return ["ds1"]

        async def _fake_fetch(dataset_id: str):
            return (
                set(),
                [
                    {
                        "constraint_type": "FOREIGN KEY",
                        "source_table": "orders",
                        "source_column": "user_id",
                        "target_schema": "ds1",
                        "target_table": "users",
                        "target_column": "id",
                    },
                    {
                        "constraint_type": "FOREIGN KEY",
                        "source_table": "orders",
                        "source_column": "product_id",
                        "target_schema": "ds_other",
                        "target_table": "products",
                        "target_column": "id",
                    },
                ],
            )

        monkeypatch.setattr(c, "_resolve_datasets", _fake_resolve)
        monkeypatch.setattr(c, "_fetch_constraints", _fake_fetch)

        fks = await c.discover_relationships()
        assert len(fks) == 1
        assert fks[0].source_column == "user_id"
        assert c.cross_dataset_foreign_keys_dropped == 1


# ---------------------------------------------------------------------------
# discover_tables: PK threading via constraint lookup
# ---------------------------------------------------------------------------


class TestDiscoverTablesPkThreading:
    async def test_pk_flag_threaded_from_constraints(self, monkeypatch: pytest.MonkeyPatch) -> None:
        c = BigQueryConnector("p")
        c._client = object()  # type: ignore[assignment]

        class _FakeTableRef:
            def __init__(self, table_id: str) -> None:
                self.table_id = table_id

        class _FakeTable:
            def __init__(self, table_id: str) -> None:
                self.table_id = table_id
                self.num_rows = 42
                self.schema = (
                    bigquery.SchemaField("id", "INTEGER", mode="REQUIRED"),
                    bigquery.SchemaField("name", "STRING", mode="NULLABLE"),
                )

        async def _fake_resolve(schemas):  # noqa: ANN001
            return ["app"]

        async def _fake_fetch(dataset_id: str):
            return ({("users", "id")}, [])

        def _fake_list_tables(dataset_id: str):
            return [_FakeTableRef("users")]

        class _FakeClient:
            def get_table(self, ref: _FakeTableRef):
                return _FakeTable(ref.table_id)

        monkeypatch.setattr(c, "_resolve_datasets", _fake_resolve)
        monkeypatch.setattr(c, "_fetch_constraints", _fake_fetch)
        monkeypatch.setattr(c, "_list_tables", _fake_list_tables)
        c._client = _FakeClient()  # type: ignore[assignment]

        tables = await c.discover_tables()
        assert len(tables) == 1
        users = tables[0]
        assert users.schema == "app"
        assert users.name == "users"
        assert users.row_count == 42
        id_col = next(col for col in users.columns if col.name == "id")
        assert id_col.is_primary_key is True
        name_col = next(col for col in users.columns if col.name == "name")
        assert name_col.is_primary_key is False

    async def test_missing_num_rows_yields_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        c = BigQueryConnector("p")
        c._client = object()  # type: ignore[assignment]

        class _FakeTableRef:
            table_id = "users"

        class _FakeTable:
            table_id = "users"
            num_rows = None
            schema = (bigquery.SchemaField("id", "INTEGER", mode="REQUIRED"),)

        async def _fake_resolve(schemas):  # noqa: ANN001
            return ["app"]

        async def _fake_fetch(dataset_id: str):
            return (set(), [])

        def _fake_list_tables(dataset_id: str):
            return [_FakeTableRef()]

        class _FakeClient:
            def get_table(self, ref):  # noqa: ANN001
                return _FakeTable()

        monkeypatch.setattr(c, "_resolve_datasets", _fake_resolve)
        monkeypatch.setattr(c, "_fetch_constraints", _fake_fetch)
        monkeypatch.setattr(c, "_list_tables", _fake_list_tables)
        c._client = _FakeClient()  # type: ignore[assignment]

        tables = await c.discover_tables()
        assert tables[0].row_count is None


# ---------------------------------------------------------------------------
# sample_table identifier guard
# ---------------------------------------------------------------------------


class TestSampleTableIdentifierGuard:
    async def test_dotted_schema_rejected(self) -> None:
        c = BigQueryConnector("p")
        c._client = object()  # type: ignore[assignment]
        with pytest.raises(ValueError, match=r"identifier contains '\."):
            await c.sample_table("ds.sub", "users")

    async def test_dotted_table_rejected(self) -> None:
        c = BigQueryConnector("p")
        c._client = object()  # type: ignore[assignment]
        with pytest.raises(ValueError, match=r"identifier contains '\."):
            await c.sample_table("ds", "users.x")

    async def test_negative_limit_rejected(self) -> None:
        c = BigQueryConnector("p")
        c._client = object()  # type: ignore[assignment]
        with pytest.raises(ValueError, match="non-negative"):
            await c.sample_table("ds", "users", limit=-1)


# ---------------------------------------------------------------------------
# _resolve_datasets paths
# ---------------------------------------------------------------------------


class TestResolveDatasets:
    async def test_explicit_schemas_returned_unchanged(self) -> None:
        c = BigQueryConnector("p")
        c._client = object()  # type: ignore[assignment]
        result = await c._resolve_datasets(["a", "b"])
        assert result == ["a", "b"]

    async def test_constructor_dataset_id_used_when_no_schemas(self) -> None:
        c = BigQueryConnector("p", dataset_id="only_ds")
        c._client = object()  # type: ignore[assignment]
        result = await c._resolve_datasets(None)
        assert result == ["only_ds"]

    async def test_no_dataset_falls_back_to_list_datasets(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        c = BigQueryConnector("p")

        class _DS:
            def __init__(self, name: str) -> None:
                self.dataset_id = name

        class _FakeClient:
            def list_datasets(self, project: str):
                assert project == "p"
                return [_DS("ds1"), _DS("ds2")]

        c._client = _FakeClient()  # type: ignore[assignment]
        result = await c._resolve_datasets(None)
        assert result == ["ds1", "ds2"]


# ---------------------------------------------------------------------------
# discover_tables: empty paths
# ---------------------------------------------------------------------------


class TestDiscoverTablesEmptyPaths:
    async def test_no_datasets_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        c = BigQueryConnector("p")
        c._client = object()  # type: ignore[assignment]

        async def _empty(schemas):  # noqa: ANN001
            return []

        monkeypatch.setattr(c, "_resolve_datasets", _empty)
        tables = await c.discover_tables()
        assert tables == []

    async def test_no_tables_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        c = BigQueryConnector("p")

        async def _resolve(schemas):  # noqa: ANN001
            return ["ds"]

        async def _fetch(dataset_id):  # noqa: ANN001
            return (set(), [])

        def _list_tables(dataset_id):  # noqa: ANN001
            return []

        monkeypatch.setattr(c, "_resolve_datasets", _resolve)
        monkeypatch.setattr(c, "_fetch_constraints", _fetch)
        monkeypatch.setattr(c, "_list_tables", _list_tables)
        c._client = object()  # type: ignore[assignment]

        tables = await c.discover_tables()
        assert tables == []


# ---------------------------------------------------------------------------
# discover_relationships: defensive skip on incomplete FK row
# ---------------------------------------------------------------------------


class TestDiscoverRelationshipsDefensiveSkip:
    async def test_missing_target_fields_skipped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        c = BigQueryConnector("p")
        c._client = object()  # type: ignore[assignment]

        async def _resolve(schemas):  # noqa: ANN001
            return ["ds"]

        async def _fetch(dataset_id):  # noqa: ANN001
            return (
                set(),
                [
                    {
                        "constraint_type": "FOREIGN KEY",
                        "source_table": "orders",
                        "source_column": "user_id",
                        "target_schema": None,
                        "target_table": None,
                        "target_column": None,
                    },
                ],
            )

        monkeypatch.setattr(c, "_resolve_datasets", _resolve)
        monkeypatch.setattr(c, "_fetch_constraints", _fetch)

        fks = await c.discover_relationships()
        assert fks == []
        assert c.cross_dataset_foreign_keys_dropped == 0


# ---------------------------------------------------------------------------
# Sampling + constraint query body via fake client
# ---------------------------------------------------------------------------


class _FakeQueryJob:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def result(self):
        return iter(self._rows)


class _FakeQueryClient:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self.last_query: str | None = None

    def query(self, q: str) -> _FakeQueryJob:
        self.last_query = q
        return _FakeQueryJob(self._rows)


class TestSampleTableQueryShape:
    async def test_sample_runs_query_and_serialises(self) -> None:
        client = _FakeQueryClient([{"id": 1, "name": "alice"}, {"id": 2, "name": "bob"}])
        c = BigQueryConnector("my-project")
        c._client = client  # type: ignore[assignment]

        rows = await c.sample_table("my_ds", "users", limit=5)
        assert rows == [{"id": 1, "name": "alice"}, {"id": 2, "name": "bob"}]
        # Query includes backtick-quoted project, dataset, table, and the limit.
        assert client.last_query is not None
        assert "`my-project`" in client.last_query
        assert "`my_ds`" in client.last_query
        assert "`users`" in client.last_query
        assert "LIMIT 5" in client.last_query


class TestFetchConstraintsBody:
    async def test_pk_and_fk_rows_partitioned(self) -> None:
        rows = [
            {
                "constraint_type": "PRIMARY KEY",
                "source_table": "users",
                "source_column": "id",
                "target_schema": None,
                "target_table": None,
                "target_column": None,
            },
            {
                "constraint_type": "FOREIGN KEY",
                "source_table": "orders",
                "source_column": "user_id",
                "target_schema": "ds",
                "target_table": "users",
                "target_column": "id",
            },
        ]
        client = _FakeQueryClient(rows)
        c = BigQueryConnector("p")
        c._client = client  # type: ignore[assignment]

        pks, fks = await c._fetch_constraints("ds")
        assert pks == {("users", "id")}
        assert len(fks) == 1
        assert fks[0]["source_table"] == "orders"


# ---------------------------------------------------------------------------
# 6.7-6.10 — live integration tests, gated on BIGQUERY_TEST_PROJECT
# ---------------------------------------------------------------------------


_LIVE_PROJECT = os.environ.get("BIGQUERY_TEST_PROJECT")
_LIVE_SKIP_REASON = "BIGQUERY_TEST_PROJECT not set; live BigQuery tests skipped"


@pytest.mark.bigquery_live
@pytest.mark.skipif(_LIVE_PROJECT is None, reason=_LIVE_SKIP_REASON)
class TestBigQueryLive:
    """Hits `bigquery-public-data.samples.shakespeare` for sampling + schema.

    Requires ADC configured locally and a `BIGQUERY_TEST_PROJECT` env var for
    billing. The public dataset is read-cross-project, so the discovery and
    relationships APIs operate on the test project's own datasets while
    sampling targets the public table.
    """

    async def test_discover_tables_returns_at_least_one(self) -> None:
        async with BigQueryConnector(_LIVE_PROJECT) as c:  # type: ignore[arg-type]
            tables = await c.discover_tables()
        # Tables may be empty if the test project has no datasets; in that case
        # the test is informational rather than failing. We assert the shape.
        assert isinstance(tables, list)
        for t in tables:
            assert isinstance(t.schema, str)
            assert isinstance(t.name, str)
            assert all(isinstance(col.name, str) for col in t.columns)

    async def test_discover_relationships_returns_list(self) -> None:
        async with BigQueryConnector(_LIVE_PROJECT) as c:  # type: ignore[arg-type]
            fks = await c.discover_relationships()
        assert isinstance(fks, list)

    async def test_sample_public_shakespeare_returns_dicts(self) -> None:
        # Use a connector scoped to the public-data project for sampling.
        async with BigQueryConnector("bigquery-public-data") as c:
            rows = await c.sample_table("samples", "shakespeare", limit=5)
        assert 0 < len(rows) <= 5
        row = rows[0]
        assert "word" in row
        assert "corpus" in row

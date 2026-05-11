# bigquery-connector Design

## Context

Sonar has three connectors: Postgres (async, psycopg3), Snowflake (sync driver wrapped in `asyncio.to_thread`), and DuckDB (same async wrapper pattern). All three follow the same observable contract: async context manager, `discover_tables`, `discover_relationships`, `sample_table`. BigQuery is a fully-managed cloud data warehouse — no local connection, REST API rather than SQL cursor, and a three-level identity hierarchy (project → dataset → table) where other systems use two levels (database → schema → table, or schema → table for file-based DBs).

The relevant Google library is `google-cloud-bigquery`. It is not a dbapi2 driver — it is a higher-level client SDK. Discovery and sampling both go through this client, with blocking calls wrapped in `asyncio.to_thread` to match the async contract.

## Goals / Non-Goals

**Goals:**
- Add `BigQueryConnector` with the same observable contract as the existing connectors.
- REST API-based schema discovery: `list_datasets`, `list_tables`, `get_table`.
- FK/PK constraint discovery via per-dataset `INFORMATION_SCHEMA` SQL queries.
- Row sampling via `SELECT * LIMIT N` using `client.query()`.
- CLI dispatch via `bigquery://PROJECT[/DATASET]` prefix and bare `bigquery` keyword.
- Top-level columns only; nested RECORD field sub-schemas serialized into `data_type` string.
- Auth exclusively via Application Default Credentials (ADC) — no credentials in DSN.

**Non-Goals:**
- TABLESAMPLE — removed from the roadmap description; YAGNI for 5-row samples.
- BigQuery Storage API (direct Arrow reads) — not needed for schema discovery or 5-row samples.
- BigQuery ML or BI Engine tables — no special handling.
- Cross-project discovery — one project per connector instance.
- Location filtering — deferred; can be added as `?location=` query param in a later change.

## Decisions

### D1: REST API for schema discovery, not INFORMATION_SCHEMA SQL

The three previous connectors all use INFORMATION_SCHEMA SQL for discovery. BigQuery breaks this pattern.

The alternative (Path B) appears consistent — use `project.region-X.INFORMATION_SCHEMA.COLUMNS` — but it is broken by design for multi-region projects:

1. `client.list_datasets()` — required to enumerate datasets (there is no SQL-only path)
2. `client.get_dataset()` per dataset — required to determine each dataset's `.location`
3. Group by location, fire one INFORMATION_SCHEMA query per unique region

Path B trades O(N) `get_table()` calls for O(D) `get_dataset()` calls plus per-region SQL queries. The API client is unavoidable; SQL adds net complexity. Additionally, INFORMATION_SCHEMA flattens RECORD/STRUCT fields to dot-notation strings (`address.city`, `address.zip`), making tree reconstruction non-trivial and error-prone on edge cases. `get_table()` returns native `SchemaField` objects with recursive `.fields` — the nesting is already parsed.

Metadata API calls are free (or sub-cent flat rate). INFORMATION_SCHEMA queries against `TABLE_STORAGE` are billed as query bytes, relevant for a discovery tool that may run periodically.

Path A, with concurrency-bounded parallelism (see D2), handles 50 datasets × 20 tables = 1000 `get_table()` calls in ~50 wall-clock round trips with Semaphore(20). Acceptable startup cost for a cached context layer.

Revisit when: BigQuery releases a free, cross-region INFORMATION_SCHEMA view that eliminates region detection (making Path B self-contained and non-broken).
Reversibility: expensive — different code path, test fixtures, query module structure. Evidence: explore session analysis on 2026-05-08 documented the Path A vs Path B comparison in full.

### D2: Async via `asyncio.to_thread` + `asyncio.Semaphore(20)` for parallel `get_table()` calls

BigQuery's Python client is synchronous. All blocking calls are wrapped in `asyncio.to_thread`, consistent with the Snowflake and DuckDB connectors.

For `get_table()` calls, a named coroutine `_discover_table(sem, client, tref)` wraps each call behind a shared `asyncio.Semaphore(20)`. All per-table coroutines are gathered with `asyncio.gather`. The semaphore bound of 20 is explicit rather than inherited from `_scan_pipeline` (which uses Semaphore(5) for LLM calls):

- BigQuery's `tables.get` quota is generous (hundreds of requests per second), but two API calls may be made per table in the worst case (`list_tables` + `get_table`)
- 20 concurrent calls covers the common case (hundreds of tables) without quota risk
- The named `_discover_table` coroutine makes the semaphore scope obvious and auditable

This mirrors `_scan_pipeline`'s sampling concurrency pattern (a deliberate choice, not a coincidence).

Revisit when: 429 quota errors appear in BigQuery quota dashboard for `tables.get` with large projects; raise the semaphore if quota allows.
Reversibility: cheap — adjust the bound.

### D3: Nested RECORD fields rendered as type string, top-level columns only

BigQuery `SchemaField` objects have a recursive `.fields` attribute for `RECORD` types. Two options:

- **Option A (this design)**: top-level columns only; RECORD sub-schema serialised inline as `RECORD<field TYPE, ...>`. REPEATED fields append ` REPEATED` to the type string. No structural changes to `Column` or `_reject_dotted_identifier`.
- **Option B**: flatten to dot-notation (`address.city`). Breaks `_reject_dotted_identifier`, which exists to protect the context-index bundle's on-disk key encoding from ambiguous paths.

Option A means the LLM sees the full nested structure when generating a column description — `RECORD<city STRING, zip STRING>` gives sufficient semantic content. A v2 indexing-by-nested-field feature (useful for "find tables with an email field anywhere in the schema") would require a key encoding change in context-index and is deferred until someone asks for it.

Recursive serialization: `_render_bq_type(field)` handles RECORD by recursing over `.fields` and concatenating. Maximum BigQuery nesting depth is 15 levels; recursion stack is safe.

Revisit when: a user asks for nested-field search across deeply nested schemas and the type string is not sufficient.
Reversibility: expensive — per-field indexing requires key encoding changes in context-index; any downstream consumers that have indexed the existing type strings would need re-generation.

### D4: Sampling via plain `SELECT * LIMIT N` (no TABLESAMPLE)

The roadmap entry originally mentioned TABLESAMPLE. Removed.

`TABLESAMPLE SYSTEM (p PERCENT)` selects storage blocks probabilistically. For tables smaller than a block (~1 MB), it returns 0 rows. Sonar samples 5 rows for LLM description context — the exact rows don't matter, but returning 0 rows produces a useless description. The fallback (detect 0 rows, retry with LIMIT) doubles API calls in the common case of small tables.

Plain `LIMIT 5` is cost-effective: BigQuery charges for bytes scanned, and a 5-row LIMIT on a table with columnar storage scans approximately one micro-partition — negligible cost.

Revisit when: a user reports meaningful billing charges from sampling large tables during `sonar scan`.
Reversibility: cheap — add TABLESAMPLE with threshold fallback.

### D5: FK and PK constraints via per-dataset INFORMATION_SCHEMA

BigQuery added non-enforced PK and FK constraints (as metadata only) in 2022. Most datasets will have none. Discovery proceeds per dataset:

```sql
-- per dataset (project.dataset.INFORMATION_SCHEMA):
SELECT tc.constraint_name, tc.constraint_type, kcu.table_name, kcu.column_name,
       rcu.table_schema AS ref_schema, rcu.table_name AS ref_table, rcu.column_name AS ref_column
FROM project.dataset.INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
JOIN project.dataset.INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu ...
LEFT JOIN project.dataset.INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS rc ...
LEFT JOIN project.dataset.INFORMATION_SCHEMA.KEY_COLUMN_USAGE rcu ...
WHERE tc.constraint_type IN ('PRIMARY KEY', 'FOREIGN KEY')
```

The dataset-scoped form (not the regional meta-table form `project.region-X.INFORMATION_SCHEMA.*`) is used for the same reason as D1 — regional form requires knowing dataset locations; per-dataset form does not.

Cross-dataset FKs are dropped (analogous to Snowflake's cross-database FK filtering). BigQuery non-enforced FKs are assumed to stay within a dataset in the common case.

Per-dataset INFORMATION_SCHEMA failures are isolated in both `discover_tables` and `discover_relationships`: a single restricted or permission-denied dataset logs a warning and is skipped, while remaining datasets continue to be discovered. The two call sites isolate independently — `discover_tables` proceeds with empty PK info for the failing dataset (columns are still returned without `is_primary_key=True`), and `discover_relationships` drops only that dataset's FKs. This avoids the failure mode where one restricted dataset in a project of many produces zero results.

Revisit when: a user reports BigQuery FK constraints that are not being discovered (check whether they are cross-dataset or use unsupported constraint types).
Reversibility: cheap — extend query or add cross-dataset resolution pass.

### D6: DSN forms and env-var precedence

Three accepted forms, explicit wins over implicit:

```
bigquery://PROJECT_ID              → all datasets
bigquery://PROJECT_ID/DATASET_ID   → scoped to one dataset
bigquery://PROJECT_ID/             → all datasets (trailing slash = no dataset)
bigquery                           → bare keyword; reads BIGQUERY_PROJECT (required)
                                     and BIGQUERY_DATASET (optional) from env
```

Env-var precedence: `bigquery://...` URL is explicit and wins; bare `bigquery` falls back to env vars. If `BIGQUERY_PROJECT` is unset for the bare form, fail immediately with a clear error naming the missing var.

Future extension: `bigquery://PROJECT_ID?location=europe-west1` query param for location-filtered discovery. Not implemented — the REST API enumerates datasets transparently across regions without a filter, and no user has asked for location scoping.

Revisit when: a user needs to filter discovery to a specific region or reports slow enumeration across many regions.
Reversibility: cheap — additive query param parsing in CLI dispatch.

### D7: Authentication via ADC exclusively

`google.cloud.bigquery.Client(project=project_id)` resolves credentials through the Application Default Credentials chain automatically: local `gcloud auth application-default login`, `GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json` env var, Workload Identity, GCE metadata server, and others.

No Sonar-level auth logic is needed. If ADC is not configured, the client raises an exception before any discovery call — this is the correct failure mode (fast, clear, not buried in a timeout). Users encountering auth issues follow Google's standard ADC documentation.

Revisit when: a user needs to pass an explicit `google.oauth2.service_account.Credentials` object from outside the env (e.g., in an SDK-embedding use case where Sonar is called programmatically with credentials already in hand).
Reversibility: cheap — add an optional `credentials` parameter to `BigQueryConnector.__init__` and pass it through to the client.

### D8: BigQuery identifier quoting in `sample_table`

BigQuery uses backtick quoting, not double-quote quoting:

```sql
SELECT * FROM `my-project`.`my_dataset`.`my_table` LIMIT 5
```

A `_bq_quote(name: str) -> str` helper wraps the identifier in backticks, escapes internal backticks with `\``, and rejects null bytes. The `_reject_dotted_identifier` pre-check from the shared types module is applied to schema and table names before quoting, maintaining the same injection protection as the other connectors.

`_bq_quote` is applied to the project ID as well as the dataset and table names. This is required because GCP project IDs can contain hyphens (common) and colons (domain-scoped projects such as `example.com:my-project`). Unquoted, hyphens are valid SQL operators and colons are ambiguous in some query contexts; backtick quoting eliminates both hazards.

Unlike Snowflake (where the database is bound at connect time and the query uses `schema.table`), BigQuery requires the full `project.dataset.table` reference in the query because the client is not session-scoped to a dataset. The project ID is stored at connector construction and included in every sample query.

`client.query().result()` returns an iterable of `google.cloud.bigquery.Row` objects (named tuples), not plain dicts. Each `Row` must be converted with `dict(row)` before being passed to `_serialize_row`. Skipping this conversion causes `_serialize_row` to receive a `Row` instance, which does not behave as a mapping for all serialization paths and will produce incorrect output on columns with non-string keys.

Revisit when: BigQuery adds parameterized table references to `client.query()` that eliminate the identifier-building surface.
Reversibility: cheap.

### D9: Separate `_bigquery_sql.py` module

Per-dataset FK/PK INFORMATION_SCHEMA queries live in `_bigquery_sql.py`, matching the `_snowflake_sql.py` and `_duckdb_sql.py` pattern. The REST API discovery path needs no SQL.

Revisit when: connectors share enough SQL structure to justify a shared module.
Reversibility: cheap.

### D10: `database_label` as `project-id` or `project-id.dataset-id`

```
bigquery://project       → "project"
bigquery://project/ds    → "project.dataset"
bigquery with env vars   → "project" or "project.dataset"
```

BigQuery's own notation for qualified names uses dots (`project.dataset.table`). The label mirrors this. Project IDs contain hyphens (`my-project-123`) but no dots, so dot is an unambiguous separator.

Revisit when: label format causes confusion for a downstream consumer (e.g., the context-index bundle filename or MCP tool surface).
Reversibility: cheap.

## Risks / Trade-offs

- **`num_rows` accuracy**: BigQuery's metadata count can lag after streaming inserts. Same caveat as Postgres `reltuples`. Surfaced as `row_count: int | None` — downstream consumers handle `None`.
- **Per-dataset INFORMATION_SCHEMA quota**: Each constraint query is a billed SQL query (schema queries are typically sub-MB but not free). On a project with 50+ datasets, constraint discovery makes 50+ queries. For most projects (no declared constraints), this wasted cost is unavoidable. The alternative is a single metadata API call — but BigQuery has no REST endpoint for FK constraints.
- **ADC dependency for tests**: Integration tests require a real GCP project and configured ADC. Gated on `BIGQUERY_TEST_PROJECT` env var, same as `snowflake_live`. Unit tests use `SchemaField` objects constructed directly.

## Open Questions

None — all decisions above were settled during the explore session on 2026-05-08.

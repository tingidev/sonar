## Why

BigQuery is the dominant cloud data warehouse for GCP-native teams, and a significant share of real-world analytical schemas live there. Sonar has Postgres, Snowflake, and DuckDB — BigQuery completes Phase 3's connector breadth goal and makes the tool viable for GCP-first organisations.

## What Changes

- New `BigQueryConnector` class: schema discovery via BigQuery REST API (`list_datasets`, `list_tables`, `get_table`), FK/PK constraints via dataset-scoped `INFORMATION_SCHEMA`, row sampling via `SELECT * LIMIT N`.
- New DSN forms: `bigquery://PROJECT_ID`, `bigquery://PROJECT_ID/DATASET_ID`, and bare `bigquery` keyword reading `BIGQUERY_PROJECT` / `BIGQUERY_DATASET` env vars.
- New optional dependency: `google-cloud-bigquery`. Install hint: `pip install 'sonar[bigquery]'`.
- New `_bigquery_sql.py` module for FK/PK constraint queries.
- CLI dispatch extended: `_select_connector` handles `bigquery://` prefix and bare `bigquery` keyword.
- `pyproject.toml`: new optional dep + `bigquery` extras group.

## Capabilities

### New Capabilities

- `bigquery-connector`: Connect to BigQuery, enumerate datasets/tables/columns using the BigQuery REST API, discover FK/PK constraints via per-dataset `INFORMATION_SCHEMA`, sample rows with `SELECT * LIMIT N`. Nested `RECORD` fields rendered as type strings (`RECORD<field TYPE, ...>`). Authentication exclusively via Application Default Credentials (ADC).

### Modified Capabilities

(none — no existing spec-level behaviour changes)

## Impact

- `src/sonar/connectors/bigquery.py` — new connector
- `src/sonar/connectors/_bigquery_sql.py` — FK/PK constraint SQL
- `src/sonar/cli.py` — dispatch extended, two new install-hint functions
- `pyproject.toml` — optional dep + extras group
- `tests/test_bigquery_connector.py` — new test file

<p align="center">
  <h1 align="center">Sonar</h1>
  <p align="center">
    Data context for AI agents.
    <br />
    Connect a database. Get agent-ready context in minutes. No manual curation.
  </p>
</p>

<p align="center">
  <a href="https://pypi.org/project/sonar-ai/"><img src="https://img.shields.io/pypi/v/sonar-ai?color=blue" alt="PyPI" /></a>
  <a href="https://pypi.org/project/sonar-ai/"><img src="https://img.shields.io/pypi/pyversions/sonar-ai" alt="Python" /></a>
  <a href="https://github.com/tingidev/sonar/blob/main/LICENSE"><img src="https://img.shields.io/github/license/tingidev/sonar" alt="License" /></a>
</p>

---

```bash
pip install sonar-ai

# Scan a Postgres database — discovers schema, generates semantic descriptions via LLM
sonar scan postgresql://user:pass@localhost/mydb

# Expose the result as MCP tools for any AI agent
sonar serve
```

That's it. Your agent now understands your database — every table, column, relationship, and what they mean.

## The Problem

Every company deploying AI agents hits the same wall: **the agent doesn't understand the data.** Developers hardcode context per deployment — manual schema descriptions, custom retrieval logic, hand-built relationship maps. There's no standard tool for this.

RAG frameworks handle document retrieval. But structured data navigation — understanding what tables exist, what columns mean, how entities relate — is unsolved.

**Without Sonar**, you write this for every database, every deployment:

```python
# Manual context that breaks when the schema changes
TABLES = {
    "molecule_dictionary": "Contains pharmaceutical compounds with their identifiers...",
    "activities": "Stores bioactivity measurements from assays...",
    # ... repeat for every table, every column, every relationship
}
```

**With Sonar**, you run two commands:

```bash
sonar scan postgresql://localhost/chembl    # 73 tables described in ~2 minutes
sonar serve                                 # Agent-ready MCP server
```

## Demo: Agent Navigating a Real Database

ChEMBL is a public pharmaceutical database: 73 tables, 495 columns, 74 million rows, 22 GB on disk. An agent that has never seen this database before asks:

> *"What's the join path from a molecule to its biological targets?"*

**Without Sonar**, the agent must dump the entire schema (257 KB / ~66K tokens) and guess column meaning from names alone. `molregno`? `tid`? `assay_id`? No descriptions, no domain context. The agent either hallucinates a path or asks the user to explain the schema.

**With Sonar**, the agent calls MCP tools and navigates autonomously:

```
Agent: search("target")
  → 12 matches, including target_dictionary, target_components, target_relations

Agent: describe("target_dictionary")
  → "A reference dictionary of pharmaceutical and biological targets
     used in drug discovery research. Each row represents a unique
     molecular target that can be modulated by drugs."

Agent: relationships("target_dictionary")
  → assays.tid → target_dictionary.tid (+ 8 more incoming FKs)

Agent: describe("assays")
  → "Catalogs biochemical and pharmacological assays conducted in
     research studies, tracking test methodologies, biological targets,
     organisms, and confidence metrics."

Agent: relationships("assays")
  → activities.assay_id → assays.assay_id (+ 14 more FKs)

Agent: describe("activities")
  → "Stores experimental bioactivity measurements from chemical assays,
     capturing the results of testing compounds against biological targets."
  → activities.molregno → molecule_dictionary.molregno

Agent concludes:
  molecule_dictionary → activities → assays → target_dictionary
  (via molregno, assay_id, tid)
```

7 tool calls. 26 KB of context. The agent traced a 4-table join path across a database it had never seen, using semantic descriptions to understand what each table means — not just how they're linked.

### Context efficiency

Sonar gives the agent targeted, semantically rich context per question instead of dumping the entire schema.

Measured on ChEMBL 36 (73 tables, 495 columns, 22 GB):

| Scenario | Tool calls | Context delivered | vs. raw schema dump |
|----------|:----------:|:-----------------:|:-------------------:|
| Find molecule-related data | 3 | 12.6 KB | 4.9% |
| Trace join path molecules to targets | 7 | 26.2 KB | 10.2% |
| Full database table listing | 1 | 4.9 KB | 1.9% |
| Understand bioactivity data | 4 | 16.0 KB | 6.2% |
| Drug classification and mechanisms | 5 | 18.5 KB | 7.2% |

**Baseline:** raw schema dump (DDL + column definitions + FK constraints) = 257 KB, ~66K tokens, zero semantic information.

**Average Sonar query:** 16 KB, ~4K tokens, with descriptions, domain hints, grain, PII flags, and semantic types included.

Even the worst case — calling `describe` on all 73 tables — produces 178 KB with full semantic context, smaller than the 229 KB raw DDL that has none.

## What It Does

Sonar connects to your database, discovers the full schema (tables, columns, types, foreign keys), samples representative data, and uses an LLM to generate semantic descriptions of what everything means. The result is a portable `.sonar/` bundle — a complete data map your agents can query through MCP tools.

```
Database              Sonar                        Your Agent
─────────            ─────                        ──────────
73 tables    ──scan──►  .sonar/ bundle    ──serve──►  5 MCP tools
495 columns            descriptions.json            discover
94 FKs                 relationships.json           describe
                       tables.json                  relationships
                       meta.json                    search
                                                    sample
```

### Real output from scanning ChEMBL (public pharma database, 73 tables)

**`discover`** — list all tables in the database:

```json
[
  {"schema": "public", "name": "molecule_dictionary", "row_count": null},
  {"schema": "public", "name": "activities", "row_count": null},
  {"schema": "public", "name": "compound_structures", "row_count": null}
]
```

**`describe`** — semantic description of a single table, generated by LLM:

```json
{
  "schema": "public",
  "name": "molecule_dictionary",
  "row_count": null,
  "description": "A comprehensive chemical compound registry that catalogs pharmaceutical and research molecules with their properties, regulatory status, and drug development characteristics.",
  "grain": "One row per unique chemical compound with its identifiers, classification, and regulatory properties.",
  "domain_hints": ["pharma", "chemistry", "drug-discovery", "cheminformatics"],
  "confidence": 0.92,
  "columns": [
    {
      "name": "chembl_id",
      "data_type": "character varying",
      "nullable": false,
      "is_primary_key": false,
      "foreign_key": null,
      "description": "Unique ChEMBL public identifier for the molecule.",
      "semantic_type": "identifier",
      "pii_risk": "none",
      "confidence": 0.99
    },
    {
      "name": "pref_name",
      "data_type": "character varying",
      "nullable": true,
      "is_primary_key": false,
      "foreign_key": null,
      "description": "Preferred chemical or drug name for the molecule.",
      "semantic_type": "dimension",
      "pii_risk": "none",
      "confidence": 0.92
    }
  ]
}
```

**`relationships`** — foreign keys and inferred relationships:

```json
[
  {
    "source_schema": "public",
    "source_table": "activities",
    "source_column": "molregno",
    "target_schema": "public",
    "target_table": "molecule_dictionary",
    "target_column": "molregno",
    "kind": "declared"
  },
  {
    "source_schema": "public",
    "source_table": "compound_structures",
    "source_column": "molregno",
    "target_schema": "public",
    "target_table": "molecule_dictionary",
    "target_column": "molregno",
    "kind": "declared"
  }
]
```

**`search`** — find tables by name, column name, or description content:

```json
[
  {"schema": "public", "table": "activities", "match_type": "table_name"},
  {"schema": "public", "table": "activity_properties", "match_type": "table_name"},
  {"schema": "public", "table": "activity_stds_lookup", "match_type": "table_name"}
]
```

## Quickstart

### Prerequisites

- Python 3.11+
- An LLM provider — any of the following:
  - **Local model via Ollama** (recommended for sensitive data — nothing leaves your machine)
  - Anthropic API key (Claude Haiku)
  - OpenAI API key
  - Any OpenAI-compatible endpoint (vLLM, LiteLLM, etc.)

### Install

```bash
pip install sonar-ai
```

### Scan a database

**With a local model (zero data leakage):**

```bash
# Start Ollama with a capable model (e.g. Qwen 3.6 27B, Llama 3.3, Mistral)
ollama pull qwen3.6:27b

# Scan — your data never leaves the machine
sonar scan --model qwen3.6:27b --base-url http://localhost:11434/v1 \
  postgresql://user:pass@localhost/mydb
```

**With a cloud provider:**

```bash
export ANTHROPIC_API_KEY=sk-ant-...
sonar scan postgresql://user:pass@localhost/mydb

# Or with OpenAI
export OPENAI_API_KEY=sk-...
sonar scan --model gpt-4.1-mini postgresql://user:pass@localhost/mydb
```

```bash
# Control concurrency for rate-limited APIs or local models
sonar scan --concurrency 2 postgresql://user:pass@localhost/mydb
```

### Scan Snowflake

The Snowflake driver is a separate install — Postgres-only users don't pay the
download cost.

```bash
pip install 'sonar-ai[snowflake]'
```

Two ways to authenticate. Pick one:

```bash
# 1) URL form — fastest for a one-off scan, but the password is visible in
#    shell history and `ps` output. Prefer the env-var form for anything
#    beyond a quick test.
sonar scan 'snowflake://USER:PASS@ACCOUNT/DATABASE/SCHEMA?warehouse=W&role=R'

# 2) Bare keyword form — reads SNOWFLAKE_* env vars (table below), supports
#    password, key-pair, OAuth, and externalbrowser SSO authentication.
export SNOWFLAKE_ACCOUNT=xy12345.us-east-1.aws
export SNOWFLAKE_USER=joeri
export SNOWFLAKE_PASSWORD=...
export SNOWFLAKE_DATABASE=SNOWFLAKE_SAMPLE_DATA
export SNOWFLAKE_SCHEMA=TPCH_SF1
export SNOWFLAKE_WAREHOUSE=COMPUTE_WH
sonar scan snowflake
```

Sonar reads exactly these environment variables (everything outside the list
is silently ignored, so a future driver renaming a parameter never silently
changes the contract):

| Variable | Driver kwarg | Purpose |
|---|---|---|
| `SNOWFLAKE_ACCOUNT` | `account` | **Required.** Account locator. |
| `SNOWFLAKE_USER` | `user` | **Required.** Username. |
| `SNOWFLAKE_DATABASE` | `database` | **Required.** Bound database. Sonar scans within one database per invocation. |
| `SNOWFLAKE_AUTHENTICATOR` | `authenticator` | One of: `snowflake` (default), `externalbrowser`, `oauth`, `snowflake_jwt`. |
| `SNOWFLAKE_PASSWORD` | `password` | Password authentication. |
| `SNOWFLAKE_PRIVATE_KEY_PATH` | `private_key_file` | Key-pair authentication — path to PEM file. |
| `SNOWFLAKE_PRIVATE_KEY_PASSPHRASE` | `private_key_file_pwd` | Key-pair passphrase, if encrypted. |
| `SNOWFLAKE_TOKEN` | `token` | OAuth bearer token. |
| `SNOWFLAKE_SCHEMA` | `schema` | Optional schema scope; otherwise all non-system schemas in the database. |
| `SNOWFLAKE_WAREHOUSE` | `warehouse` | Optional warehouse override. |
| `SNOWFLAKE_ROLE` | `role` | Optional role override. |

Snowflake declares foreign keys as informational only, so most warehouses don't
declare them at all. The `inferred-relationships` heuristic (Phase 2) recovers
much of the FK graph from naming patterns alone — that compounds with this
connector to give Snowflake users the same agent-ready graph that Postgres
users get from declared FKs.

### Scan BigQuery

The BigQuery driver is a separate install:

```bash
pip install 'sonar-ai[bigquery]'
```

Authenticate with Application Default Credentials (recommended) or a service
account key file:

```bash
# ADC — authenticate once with gcloud, then scan
gcloud auth application-default login

# Option 1: DSN form — project ID only (scans all datasets in the project)
sonar scan bigquery://my-project-id

# Option 2: DSN form — project ID + dataset (scans one dataset)
sonar scan bigquery://my-project-id/my_dataset

# Option 3: Bare keyword — reads BIGQUERY_PROJECT and BIGQUERY_DATASET env vars
export BIGQUERY_PROJECT=my-project-id
export BIGQUERY_DATASET=my_dataset   # optional; omit to scan all datasets
sonar scan bigquery
```

If you use a service account key file instead of ADC, set the standard Google
environment variable before scanning:

```bash
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account-key.json
sonar scan bigquery://my-project-id
```

BigQuery datasets are mapped to Sonar schemas. Foreign key constraints are
extracted from `INFORMATION_SCHEMA` per dataset; cross-dataset FKs are excluded
(BigQuery does not enforce them at the storage layer).

### Start the MCP server

**Bundle-only mode** — stateless, no database credentials needed:

```bash
sonar serve
```

**Live mode** — adds the `sample` tool for querying live rows:

```bash
sonar serve postgresql://user:pass@localhost/mydb
```

### Connect to Claude Code

Add to your `.mcp.json`:

```json
{
  "mcpServers": {
    "sonar": {
      "command": "sonar",
      "args": ["serve", "--bundle-dir", "/path/to/.sonar/"]
    }
  }
}
```

Then ask your agent: *"What tables contain information about molecules?"* — it will use the `search` and `describe` tools to navigate your data autonomously.

### Connect to Cursor

Add to `.cursor/mcp.json` in your project:

```json
{
  "mcpServers": {
    "sonar": {
      "command": "sonar",
      "args": ["serve", "--bundle-dir", "/path/to/.sonar/"]
    }
  }
}
```

## How It Works

### 1. Discovery

Sonar connects to your database and discovers the complete schema: tables, columns, data types, primary keys, foreign keys, and constraints. No access to application code needed — the database is the source of truth.

### 2. Semantic Description

For each table, Sonar samples representative rows and sends them (along with the schema) to an LLM. The LLM generates:

- **Table description** — what the table represents in business terms
- **Grain** — what one row means
- **Domain hints** — which business domains the table belongs to
- **Column descriptions** — what each column means, not just its type
- **Semantic types** — identifier, dimension, measure, or other
- **PII classification** — none, low, medium, or high risk per column

### 3. Relationship Mapping

Foreign keys are extracted directly from the database. Additionally, Sonar infers relationships from naming patterns (e.g. `user_id` referencing `users.id`) to recover the FK graph in databases that don't declare them — common in data warehouses like Snowflake.

### 4. Bundle Generation

Everything is written to a `.sonar/` directory as plain JSON files:

```
.sonar/
  meta.json           # Scan metadata (source, timestamp)
  tables.json         # Raw schema (tables, columns, types, keys)
  descriptions.json   # LLM-generated semantic descriptions
  relationships.json  # Foreign keys and inferred relationships
```

The bundle is portable — commit it to a repo, share it with your team, point any MCP client at it.

### 5. MCP Server

`sonar serve` exposes the bundle as MCP tools over stdio. Five tools:

| Tool | Description | Requires DB |
|------|-------------|:-----------:|
| `discover` | List tables, optionally filtered by schema | No |
| `describe` | Full semantic description of a table | No |
| `relationships` | Foreign keys incident on a table | No |
| `search` | Substring search across names and descriptions | No |
| `sample` | Return live rows with PII redaction | Yes |

The first four tools are stateless reads over the bundle — no database connection, no credentials. The `sample` tool is registered only when a DSN is provided, opening short-lived connections per call with a hard cap (20 rows max) and automatic PII stripping.

## Data Privacy

Sonar is designed to scan databases containing sensitive data. Two levels of protection:

**Run locally, keep data local.** Point Sonar at a local LLM (Ollama, vLLM) and your data never crosses a network boundary. Schema metadata and sample rows stay on your machine. This is the recommended setup for production databases, healthcare data, financial records, or anything subject to compliance requirements.

**PII stripping on the agent side.** Even when using a cloud LLM for scanning, the MCP server that agents connect to automatically strips sensitive columns from live queries (see below).

Scanning sends table schemas and small row samples (5 rows per table) to the LLM for description generation. With a local model, this stays on-machine. With a cloud provider, evaluate whether sample rows from your database are acceptable to send to a third-party API.

## PII Handling

Sonar classifies every column by PII risk during the scan. When the `sample` tool returns live rows, columns classified as `medium` or `high` risk are automatically nulled:

```json
{
  "patient_name": null,
  "age": 45,
  "diagnosis_code": "E11.9"
}
```

The agent sees the column exists but never receives sensitive values. Override with `--allow-pii` in operator-authorized environments. Every `sample` call is logged to the `sonar.mcp.audit` logger regardless of the PII flag.

## Architecture

```
src/sonar/
  connectors/     Data source adapters (Postgres, Snowflake, DuckDB)
  engine/         Multi-provider LLM client + description generation + relationship mapping
  index/          Context bundle storage and loading
  mcp/            MCP server and tool implementations
  cli.py          CLI entrypoint
```

Sonar is async throughout (psycopg3, FastMCP), uses frozen dataclasses for immutability, and handles errors at system boundaries only.

## Roadmap

Sonar is in active development. Current status: **Phase 3 complete** — Postgres, Snowflake, DuckDB, and BigQuery connectors, multi-provider LLM support, inferred relationships, evaluation toolkit.

| Phase | Status | Scope |
|-------|--------|-------|
| **Phase 1** | Complete | Postgres connector, context engine, MCP server |
| **Phase 2** | Complete | Snowflake + DuckDB connectors, inferred relationships, evaluation toolkit |
| **Phase 3** | Complete | Multi-provider LLM (Anthropic, OpenAI, Ollama), BigQuery connector |
| **Phase 4** | Planned | UX polish, progress reporting, developer experience, community contributions |

See [ROADMAP.md](ROADMAP.md) for detailed milestones.

## Contributing

Sonar is open to contributions. The most impactful areas:

- **Connectors** — MySQL, SQLite, S3/Parquet
- **Bug reports and feature requests** — [open an issue](https://github.com/tingidev/sonar/issues)

Development setup:

```bash
git clone https://github.com/tingidev/sonar.git
cd sonar
poetry install -E snowflake
poetry run pytest
```

Tests run at ~96% coverage. The test suite includes unit tests, integration
tests against a real Postgres instance (via Docker), and MCP tool tests.

**Snowflake testing — two tiers.** Contributors don't need a Snowflake account:

- **Default tier** — Snowflake tests run against [`fakesnow`](https://github.com/tekumara/fakesnow),
  a DuckDB-backed in-process emulator that supports `INFORMATION_SCHEMA`,
  `SHOW` commands, and table sampling. Every PR runs these tests via the
  default `pytest` invocation.
- **Live tier** — real-account smoke tests tagged `@pytest.mark.snowflake_live`.
  Skipped by default; run via the `snowflake-live.yml` GitHub Actions workflow
  on push-to-main and `workflow_dispatch` only. Credentials never reach
  PR-triggered runs from forks.

fakesnow accepts more permissive SQL than real Snowflake, so a query that works
in tests can still fail against a real warehouse. The live tier is the safety
net.

## License

[Apache-2.0](LICENSE)

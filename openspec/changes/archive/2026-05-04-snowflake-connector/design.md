## Context

Sonar ships with a single connector — `PostgresConnector` — and the connector "abstraction" today is whatever happens to be public on `sonar.connectors.postgres`. Three signals make this the right moment to add the second connector:

- The shared dataclasses (`Column`, `Table`, `ForeignKey`) are imported from `connectors.postgres` in 14 places across `src/` and `tests/`. One of those imports is private (`_coerce_value` into `mcp/tools/sample_tool.py`). A second connector either makes this leak structural or forces a small refactor.
- Snowflake's foreign-key constraints are informational only — the engine doesn't enforce them, so many Snowflake users don't declare them at all. The naming-heuristic inference shipped in `inferred-relationships` (#7) is structurally most valuable on exactly this schema shape: the two features compound, and a real Snowflake scan should produce a graph that is mostly inferred. Snowflake is therefore the first connector where the inference layer earns its keep on production data.
- Phase 2's evaluation toolkit (#10) needs more than one data source to be a meaningful evaluator.

The connector contract today is small (4 async methods + lifecycle), the data shapes are stable, and no consumer in the codebase calls connectors polymorphically — every call site instantiates a specific connector class.

## Goals / Non-Goals

**Goals:**

- Ship a working `SnowflakeConnector` covering schema discovery, foreign-key extraction, and row sampling.
- Make `snowflake-connector-python` a strictly optional install — Postgres-only users do not pay the install cost or import time.
- Surface missing-driver errors at CLI dispatch, before any credentials are read or any connection is attempted.
- Eliminate the private cross-import (`_coerce_value`) by extracting shared connector code into connector-agnostic modules.
- Keep the bundle, context-index, and MCP tool surface byte-identical between Postgres and Snowflake outputs.
- Match the Postgres CLI ergonomics: dispatch is positional-argument-based, no flags, no ambient-environment auto-detection.

**Non-Goals:**

- A formal `Connector` Protocol or ABC. Two implementations is not yet enough demand to commit to a polymorphic interface.
- Cross-database scans in a single invocation. One `sonar scan` targets one Snowflake database (× its schemas).
- A `snowsql`/`dbt`-style profile-config-file system. Env vars carry the long tail of auth options for now.
- Native async via `snowflake.connector.aio`. We use `asyncio.to_thread` against the sync driver and revisit when the async variant is the boring choice.
- A live-Snowflake-account integration test in CI. Tests run offline against mocked drivers and recorded fixtures.
- Cross-connector inference adjustments. `relationship-mapping` already works on the shared `Table`/`ForeignKey` types, unchanged.

## Decisions

### D1. Shared types module, no Protocol

Extract `Column`, `Table`, `ForeignKey` into `sonar/connectors/types.py` and the row-coercion helper into `sonar/connectors/serialize.py`. Both connectors import from there. No `Connector` Protocol or ABC.

**Why:** The 14 import sites that today reach into `connectors.postgres` already treat the dataclasses as connector-agnostic — the only thing wrong is the import path. Moving them to a shared module fixes the leak without committing to a polymorphic interface no consumer uses. Keeping `postgres.py` and `snowflake.py` as siblings means each connector class is referenced directly at construction time (`cli.py` dispatch); polymorphism is unnecessary.

**Alternatives:**

- *No abstraction* (sibling `SnowflakeConnector`, types still in `postgres.py`): leaves the leak structural and pins 14 import sites to a connector-specific path. Adds friction to every future connector and feels like deferred debt.
- *Full `Connector` Protocol* (formal interface, factory registry): adds a layer for future swappability that no consumer demands today. Speculative under freeze discipline.

Revisit when: a third connector lands or a consumer needs to call connectors polymorphically (e.g. a multi-source scan command).
Reversibility: cheap (the Protocol can be added later by writing one file; the types are already extracted).

### D2. Identifiers are (schema, table); database lives in connector config

Snowflake names tables as `database.schema.table` (3-level), Postgres as `schema.table` (2-level). The connector binds a single Snowflake database at connection time and emits `Table`/`ForeignKey` records with 2-level keys within that database.

**Why:** Every downstream consumer (relationship-mapping, context-index bundle keys, MCP tool signatures) is shaped around 2-level identifiers. Adding `database` to the `Table` dataclass would touch all of them and would change the on-disk bundle format — a freeze-discipline expensive reversal that gives us "scan multiple databases at once," which is not a feature any user has asked for. Snowflake's own user mental model also lives within one database at a time (`USE DATABASE`).

**Alternatives:**

- *Add `database: str | None` to `Table`*: touches every consumer; persisted bundle format changes; expensive reversal for a feature with no concrete demand.
- *Encode database as `"DB.SCHEMA"` in the schema field*: violates the existing `_reject_dotted_identifier` rule; downstream string parsing would have to learn the encoding. Ugly and fragile.

**Cross-database FK behaviour:** Snowflake's INFORMATION_SCHEMA can expose foreign keys whose target lives in a different database than the connector's bound one. Those are dropped (not emitted as partial records) — but dropped FKs are not buried in log lines alone. The `sonar scan` summary report includes the count and a one-line note ("3 foreign keys reference tables outside database `<DB>` and were excluded"), so the user sees the gap without having to grep logs. The scan completes; the user is informed; the user can decide whether to re-scan against the other database.

Revisit when: a user explicitly asks to span multiple Snowflake databases in one scan, or when the evaluation toolkit (#10) needs a multi-database test fixture.
Reversibility: cheap — adding an optional `database` field is additive; bundles without it stay valid.

### D3. Dispatch grammar: positional URL or bare keyword, no flag

`sonar scan` accepts three positional forms:

- `postgresql://...` and `postgres://...` → `PostgresConnector` (unchanged).
- `snowflake://USER:PASS@ACCOUNT/DB/SCHEMA?warehouse=...&role=...` → `SnowflakeConnector` with password auth.
- `snowflake` (bare keyword) → `SnowflakeConnector` reading `SNOWFLAKE_*` env vars.

No `--snowflake` flag, no auto-detection from ambient environment.

**Why:** Auto-detecting from `SNOWFLAKE_ACCOUNT` would silently switch connectors for any user with day-job env vars set. Explicit positional dispatch keeps the surprise surface zero — the user always sees in their command what they targeted. The bare keyword is not pretending to be a URL because it isn't one (no creds, no account, no path); a URL form would force users to invent placeholder values for variables they're storing in env. Two clearly distinct invocations, both explicit.

**Why URL only carries password auth:** Key-pair authentication needs a PEM file path that's awkward to encode in a URL; OAuth tokens are long opaque strings; externalbrowser SSO has no credentials to embed at all. Trying to fit those into a URL invents conventions that nobody else uses.

**Curated env-var list (not driver kwargs).** The bare-keyword path reads exactly these 10 environment variables and forwards them to the driver under the corresponding `connect()` kwargs:

| Variable | Driver kwarg | Purpose |
|---|---|---|
| `SNOWFLAKE_ACCOUNT` | `account` | Required. Account locator. |
| `SNOWFLAKE_USER` | `user` | Required. Username. |
| `SNOWFLAKE_AUTHENTICATOR` | `authenticator` | One auth-mechanism selector: `snowflake` (default), `externalbrowser`, `oauth`, `snowflake_jwt`. |
| `SNOWFLAKE_PASSWORD` | `password` | Password auth. |
| `SNOWFLAKE_PRIVATE_KEY_PATH` | `private_key_file` | Key-pair auth — path to PEM file. |
| `SNOWFLAKE_PRIVATE_KEY_PASSPHRASE` | `private_key_file_pwd` | Key-pair auth — passphrase, if encrypted. |
| `SNOWFLAKE_TOKEN` | `token` | OAuth bearer token. |
| `SNOWFLAKE_DATABASE` | `database` | Required. Bound database (per D2). |
| `SNOWFLAKE_SCHEMA` | `schema` | Optional schema scope; otherwise all schemas in the database. |
| `SNOWFLAKE_WAREHOUSE` | `warehouse` | Optional warehouse override. |
| `SNOWFLAKE_ROLE` | `role` | Optional role override. |

Unknown `SNOWFLAKE_*` variables are silently ignored — the curated set is the contract. If a future driver release adds a new `connect()` kwarg we want to expose, that's a five-minute follow-up PR adding one row to this table. The cost of decoupling from the driver's kwarg surface is one table here; the win is that users don't have to update their shell config when the driver renames a parameter.

**Alternatives:**

- *`--snowflake` flag + auto-detect from env*: ambient env vars from a user's other shell (e.g. day-job dbt setup) could silently retarget `sonar scan` to a different account.
- *Single URL form covering all auth*: forces invented URL conventions for non-URL-shaped credentials (file paths, browser SSO).
- *Pass-through to driver `connect(**kwargs)` (no curation)*: ties our user-facing env-var contract to whatever the driver renames or removes; OS users would then have to mirror upstream churn in their shell configs. Curated list absorbs that churn for them.
- *Config file (snowsql/dbt-style profiles)*: large new design surface to commit to in v1.

Revisit when: a user asks for an env var that isn't in the curated list (then add one row to the table); a user reports the env-var path is too painful to manage across multiple Snowflake targets (then a profile-config-file system becomes the right next step, likely concurrent with #10's evaluation harness).
Reversibility: cheap — adding env vars is one-row-per-PR; profile config is additive on top.

### D4. Optional dependency with dispatch-time guard

`snowflake-connector-python` is gated behind `[tool.poetry.extras] snowflake`. The CLI checks for `snowflake.connector` importability at the dispatch point — before reading any credentials or env vars — and exits with an actionable `pip install sonar[snowflake]` message if it's missing. The connector class itself assumes the import succeeded.

**Why:** The Snowflake driver is heavy (transitive dependencies pull in `pyarrow`, `pyOpenSSL`, `cryptography`, large protobuf-shaped binaries). Postgres-only users should not pay that cost on a default install. Failing at the CLI dispatch point — rather than at `__aenter__` — means the user finds out *before* typing credentials, not after. The connector class itself doesn't need defensive import-availability checks; that responsibility belongs to the CLI boundary.

**Alternatives:**

- *Hard dependency*: simplest implementation but inflicts the heavy install on every user.
- *Lazy import inside `SnowflakeConnector.__aenter__`*: defers the failure past credential entry, which is a worse experience.

Revisit when: the driver becomes light enough that the install cost is negligible, or `pip install sonar` reasonably depends on it (unlikely).
Reversibility: cheap — extras can be promoted to required dependencies in one `pyproject.toml` edit.

### D5. Async via asyncio.to_thread

`snowflake-connector-python` is a synchronous library. The `aio` variant exists but is newer and less battle-tested. Calls into the driver happen inside `asyncio.to_thread` to keep `SnowflakeConnector` async-context-manager-shaped without blocking the event loop.

**Why:** Sonar is async throughout (`psycopg`, MCP, CLI). `to_thread` is the boring, proven path for adapting a sync library — overhead is irrelevant for a one-shot scan that issues a small number of large queries. Native async would buy us nothing measurable today and exposes us to driver bugs in a code path we don't control.

**Alternatives:**

- *`snowflake.connector.aio`*: less mature; the boring choice when the maturity gap closes.
- *Sync connector + sync entrypoint for the Snowflake path*: bifurcates the codebase; not worth it for one-shot scans.

Revisit when: `snowflake.connector.aio` is the upstream-recommended path for greenfield projects, or when scan duration becomes a bottleneck on large warehouses.
Reversibility: cheap — swap `to_thread` calls for direct `await` once the async variant is mature.

### D6. Test strategy: fakesnow by default, tagged live-account tests on push-to-main

Two layers, both runnable, only one required for contributors:

- **Default tier (every test run, every PR):** [`fakesnow`](https://github.com/tekumara/fakesnow) — a DuckDB-backed Snowflake emulator that supports `INFORMATION_SCHEMA`, `SHOW` commands, multi-database/schema, and table sampling. Added as a dev dependency. Every Snowflake unit and integration test runs against it. No credentials needed — contributors clone, install, run.
- **Live tier (push-to-main and manual trigger only):** real Snowflake account, gated behind `@pytest.mark.snowflake_live` and skipped by default. Credentials live in GitHub Actions secrets and are exposed only on push-to-main and `workflow_dispatch`. PRs from forks never see them. Cost is negligible — a paid Standard-tier account on metadata-only workloads runs in cents per session, and `SNOWFLAKE_SAMPLE_DATA` (TPC-DS / TPC-H) provides a realistic, FK-rich schema for free.

**Why:** The contributor model has to be "clone and run" for an OS project — gating Snowflake tests behind credentials makes external contributions hostile. fakesnow gets us a real query engine that knows the INFORMATION_SCHEMA shape, with one caveat: it accepts more permissive SQL than real Snowflake, so false positives are possible (queries that pass fakesnow but fail real Snowflake). The live tier closes that loop on push-to-main without putting credentials in PR-triggered runs.

**Alternatives:**

- *Mocked driver only*: fast and sealed but blind to INFORMATION_SCHEMA-shape surprises. Replacing it with fakesnow gives real query execution at no contributor cost.
- *Recorded query/response fixtures from a real account*: would also work, but maintenance cost is real (every driver-result-shape change requires re-capture) and we'd need a real account to capture from in the first place. fakesnow is strictly better given it exists.
- *Live-account CI on PRs*: leakage risk on forks, slow, flaky, hostile to contributors. Push-to-main + manual trigger is the right gate.

**fakesnow caveat — the false-positive risk:** because fakesnow accepts more permissive SQL than real Snowflake, a query that works in tests can still fail against a real warehouse. The live tier is the safety net. Until the live job runs (post-merge to main or manual trigger), fakesnow-green is the only signal a PR carries.

Revisit when: fakesnow's permissive-SQL gap bites us in a way the live tier didn't catch quickly enough, or fakesnow stops being maintained.
Reversibility: cheap — both layers are additive; we can drop either.

### D7. Row count from INFORMATION_SCHEMA.TABLES.ROW_COUNT

Snowflake exposes a per-table `ROW_COUNT` column in `INFORMATION_SCHEMA.TABLES`. The connector reads it during `discover_tables` and populates `Table.row_count`, falling back to `None` when missing.

**Why:** It's the cheapest available answer (no scan, no `COUNT(*)`), already part of the discovery query plan we'd run. Matches the Postgres `pg_class.reltuples` precedent — a "best-effort, may be stale" hint, not a guarantee. Same `int | None` shape, same downstream consumers.

**Alternatives:**

- *Live `COUNT(*)` per table*: guaranteed accurate but adds a query per table — costly on large warehouses, not worth it for a UX hint.
- *Skip row_count for Snowflake*: forces description engine and MCP outputs to special-case missing data; not necessary when the column is right there.

Revisit when: the description engine starts requiring exact counts (none planned), or when Snowflake users report the value is wrong often enough to mislead.
Reversibility: cheap — fall back to `None` is one branch.

### D8. Identifier case is preserved as-returned

Snowflake folds unquoted identifiers to UPPERCASE on write. When INFORMATION_SCHEMA returns table and column names, they come back uppercase. The connector preserves them as returned — bundles emit `MOLECULE_DICTIONARY` for Snowflake and `molecule_dictionary` for Postgres.

**Why:** Round-tripping queries against the warehouse (e.g. by the MCP sample tool) requires the case the warehouse expects. Lowering Snowflake names breaks downstream queries; upper-casing Postgres names breaks Postgres queries. Preserving as-returned is the only behaviour that always round-trips correctly. The cosmetic difference between bundles is acceptable — bundles are per-source.

**Alternatives:**

- *Lowercase everywhere*: silent data corruption when the MCP sample tool tries to query the lowered identifier against Snowflake.
- *Preserve and surface a `case_folding` flag on Table*: adds a field that consumers would have to learn; pure cosmetic concern.

Revisit when: a user reports confusion from bundle case differences, or when MCP-tool consumers explicitly request case normalization.
Reversibility: cheap — a normalization layer can be added downstream of discovery.

## Risks / Trade-offs

- **[Risk]** Snowflake's UPPERCASE identifiers look alien next to lowercase Postgres bundles. → *Mitigation*: D8 preserves as-returned for round-trip correctness; document the difference in the README; it's a recoverable cosmetic layer if it becomes painful.
- **[Risk]** Recorded fixtures drift from real Snowflake responses as the driver or INFORMATION_SCHEMA evolves. → *Mitigation*: keep fixtures small and focused on the queries we own; make them easy to refresh from a real account; fall back to mocked unit tests for the bulk of coverage.
- **[Risk]** Heavy install size of `snowflake-connector-python` (transitive `pyarrow`, `cryptography`, etc.) annoys users who type `pip install sonar[snowflake]` and don't expect the size. → *Mitigation*: name it explicitly in the README install instructions; leave to upstream to reduce footprint.
- **[Risk]** Env-var auth on a misconfigured shell (e.g. Snowflake variables left over from another project) connects to the wrong account. → *Mitigation*: D3 forces the user to type `snowflake` explicitly — there is no ambient auto-detection. The mistake space is "wrong env vars" not "didn't realise I was hitting Snowflake."
- **[Risk]** Snowflake's `to_thread`-wrapped sync driver holds a thread per active query. → *Mitigation*: `sonar scan` issues a small number of queries serially; this isn't a server workload.
- **[Trade-off]** Two-level identifiers (D2) means Sonar cannot scan two Snowflake databases in one invocation. Users wanting multi-database coverage run `sonar scan` per database and either keep multiple bundles or merge them downstream. We accept this in exchange for byte-identical bundle/MCP shape across connectors.
- **[Trade-off]** Password-in-URL is visible in shell history and `ps` output. Postgres has the same property and users expect it; Snowflake users coming from `snowsql`/`dbt` may not. → *Mitigation*: README adds a one-liner nudging users toward `sonar scan snowflake` + `SNOWFLAKE_PASSWORD` env var for anything beyond a quick test.

## Migration Plan

This change is purely additive at the user level. No existing bundle, command, or invocation breaks.

**Internal sequencing (within the change):**

1. Extract shared connector types and serialization helpers; update import sites; tests still green.
2. Add the optional Poetry extra; update CI to install with `-E snowflake`.
3. Implement `SnowflakeConnector` and the CLI dispatch grammar.
4. Add tests (mocked + recorded fixtures).
5. Update README and ROADMAP.

**Rollback:** revert the change. Existing Postgres bundles and invocations are unaffected because nothing about the Postgres path changes at the user level (only import paths internal to `sonar/`).

## Open Questions

- **Profile-config-file system.** When (not if) env-var setup becomes painful for users managing multiple Snowflake targets, a `~/.sonar/profiles.toml` mirroring `dbt`'s `profiles.yml` is the natural next step. Defer until a user reports it. Likely concurrent with `evaluation-toolkit` (#10) needing multiple data sources in CI. Park: ROADMAP `Deferred (Phase 2+)`.
- **Native async via snowflake.connector.aio.** Watch for upstream maturity signals; revisit D5 when the async variant is the recommended path for new projects.
- **Multi-database scans.** No concrete user ask today. If one surfaces, the additive path is to add an optional `database` field on `Table` and let the connector iterate over a list of databases. Not now.

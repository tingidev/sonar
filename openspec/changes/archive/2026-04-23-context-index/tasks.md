## 1. Bundle dataclasses

- [x] 1.1 Create `src/sonar/index/bundle.py` with `BundleMeta` frozen dataclass (fields per D4: `schema_version: int`, `generated_at: str`, `connector: str`, `database: str`).
- [x] 1.2 Add `ContextBundle` frozen dataclass in the same module composing `meta`, `tables: tuple[Table, ...]`, `descriptions: dict[tuple[str, str], TableDescription | None]`, `relationships: tuple[Relationship, ...]`.
- [x] 1.3 Define the module-level `SCHEMA_VERSION: int = 1` constant and the `BundleVersionError` / `BundleIntegrityError` exception classes in `src/sonar/index/bundle.py`.
- [x] 1.4 Unit tests in `tests/test_bundle.py`: `ContextBundle` and `BundleMeta` are frozen; `tables` / `relationships` are tuples; a bundle with a `None` description value preserves the key on equality.

## 2. ContextStore persistence

- [x] 2.1 Rewrite `src/sonar/index/store.py`: replace the `save(dict)` scaffold with `ContextStore(bundle_dir: Path)` exposing `.write(bundle: ContextBundle) -> None` and `.read() -> ContextBundle | None`.
- [x] 2.2 Implement the four-file write: `meta.json`, `tables.json`, `descriptions.json`, `relationships.json`. Use stdlib `json` only. Create `bundle_dir` if missing.
- [x] 2.3 Encode `descriptions` on disk as a JSON object keyed by `"<schema>.<name>"` (D7). Encode `None` values as JSON `null`.
- [x] 2.4 Serialise dataclasses via a small helper that uses `dataclasses.asdict` and a `default=` hook for `StrEnum` values; tuples become JSON arrays. Decode by reconstructing the frozen dataclasses explicitly in `read()` (no generic `from_dict`).
- [x] 2.5 On `read()`, return `None` if the bundle directory is missing or `meta.json` is missing. Raise `BundleVersionError` if `schema_version` does not match `SCHEMA_VERSION`. Raise `BundleIntegrityError` if any description key refers to an unknown table or any table lacks a description key (D5).
- [x] 2.6 Emit one INFO log record on `sonar.index` per successful `write` and per successful `read`. Include only integer counts (`tables`, `descriptions_present`, `descriptions_null`, `relationships`). No content, no DSN.
- [x] 2.7 Unit tests in `tests/test_store.py`: round-trip a bundle with populated and `None` descriptions; write creates missing dir; second write overwrites the first (no leftovers); unknown `schema_version` raises `BundleVersionError`; orphan description key raises `BundleIntegrityError`; missing description key raises `BundleIntegrityError`; absent bundle dir returns `None`; log record shape.

## 3. DSN sanitisation helper

- [x] 3.1 Add `_format_database_label(dsn: str) -> str` inside `src/sonar/index/bundle.py` (or adjacent) that extracts `user@host:port/dbname` from a psycopg DSN string and strips any password. Used by the scan command when populating `BundleMeta.database`.
- [x] 3.2 Unit tests for DSN sanitisation: DSN with password → label has no password; DSN without password → label matches shape; bare hostname DSN → best-effort label; unparseable input → fall back to a safe placeholder.

## 4. `sonar scan` CLI wiring

- [x] 4.1 Replace the stub body of the `scan` subcommand in `src/sonar/cli.py` with an orchestration that: (a) takes a positional `<dsn>` argument plus a `--url` named alias and a `--bundle-dir` option defaulting to `.sonar/`, (b) opens `PostgresConnector` as an async context manager, (c) calls `discover_tables`, `discover_relationships`, and per-table `sample_table`, (d) instantiates `DescriptionEngine` and awaits `describe_database`, (e) calls `map_relationships`, (f) constructs a `ContextBundle`, (g) calls `ContextStore.write`.
- [x] 4.2 Import `AnthropicClient` at module scope in `src/sonar/cli.py` (not from inside the `scan` function body) so `sonar.cli.AnthropicClient` is a stable patch target for integration tests (D11).
- [x] 4.3 Handle connector / network errors at the CLI boundary: catch at the top of `scan`, print a single error line to stderr, exit non-zero. Do not touch the bundle directory on fatal failure.
- [x] 4.4 Let per-table description failures flow through as `None` entries in the bundle — do not catch them at the CLI level; `describe_database` already handles partial success.
- [x] 4.5 Populate `BundleMeta.generated_at` with the current UTC time in ISO 8601 format; `connector="postgres"`; `database` via the sanitised label helper.

## 5. Integration test for `sonar scan`

- [x] 5.1 Add `tests/test_scan.py` as an integration test that: starts from the existing Docker fixture database (`localhost:5433`), runs the `scan` command via the CLI entry function with a `FakeLLMClient` monkeypatched onto `sonar.cli.AnthropicClient` (the documented seam per D11; reuse the fake-client pattern from `tests/test_describe.py`) and a temporary bundle dir, then asserts the four files exist and `ContextStore.read()` round-trips.
- [x] 5.2 Cover the partial-failure path in the integration test: configure the fake LLM to raise on one table; assert the scan exits 0 and that table's description serialises as JSON `null`.
- [x] 5.3 Cover the unreachable-DB failure path: point the DSN at a closed port; assert the CLI exits non-zero and no bundle files were written.
- [x] 5.4 Add a unit test for the `postgres-connector` spec delta: `discover_tables` raises `ValueError` when a returned identifier contains `"."` (simulate via a fixture or by stubbing the cursor rows); `discover_relationships` raises the same.

## 6. Project wiring

- [x] 6.1 Add `.sonar/` to `.gitignore` (D10).
- [x] 6.2 Remove the obsolete scaffold exports (if any) from `src/sonar/index/__init__.py`; re-export `ContextBundle`, `BundleMeta`, `ContextStore`, `SCHEMA_VERSION`, `BundleVersionError`, `BundleIntegrityError`.
- [x] 6.3 Run `poetry run pytest` — all existing tests still pass, new tests are green.
- [x] 6.4 Confirm coverage on `src/sonar/index/bundle.py` and `src/sonar/index/store.py` stays at or above 80% (target 100% for these pure / thin-I/O modules).
- [x] 6.5 Run `openspec validate context-index` — still passes.

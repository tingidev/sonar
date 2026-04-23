## ADDED Requirements

### Requirement: ContextBundle dataclass composes the three capability outputs

The system SHALL expose a frozen `ContextBundle` dataclass as the sole in-memory shape passed between the scan pipeline and persistence. Each `ContextBundle` SHALL carry:

- `meta: BundleMeta` — the bundle's version and provenance header.
- `tables: tuple[Table, ...]` — the raw schema as returned by the connector.
- `descriptions: dict[tuple[str, str], TableDescription | None]` — semantic descriptions keyed by `(schema, name)`. A value of `None` SHALL represent a table the description engine attempted and failed to describe.
- `relationships: tuple[Relationship, ...]` — the unified relationship graph.

The `BundleMeta` dataclass SHALL expose a bundle version, a generation timestamp, an identifier for the source connector, and a human-readable label for the scanned database. Concrete field names, types, and formatting rules are documented in `design.md`.

#### Scenario: ContextBundle is immutable

- **WHEN** a `ContextBundle` instance is constructed
- **THEN** any attempt to assign to one of its fields SHALL raise `dataclasses.FrozenInstanceError`
- **AND** the `tables` field SHALL be a `tuple`, not a `list`
- **AND** the `relationships` field SHALL be a `tuple`, not a `list`

#### Scenario: BundleMeta is immutable

- **WHEN** a `BundleMeta` instance is constructed
- **THEN** any attempt to assign to one of its fields SHALL raise `dataclasses.FrozenInstanceError`

#### Scenario: Failed descriptions are preserved as None entries

- **WHEN** a `ContextBundle` is constructed with a `descriptions` dict whose value for one key is `None`
- **THEN** that key SHALL remain present in the dict
- **AND** its value SHALL remain `None` after any round-trip through `ContextStore`

### Requirement: ContextStore persists a bundle as per-capability files under a bundle directory

The system SHALL expose `ContextStore(bundle_dir: Path)` with a method to write a `ContextBundle` to disk and a method to read one back. `write(bundle)` SHALL produce exactly four files in the bundle directory: a meta file, a tables file, a descriptions file, and a relationships file. `read()` SHALL return a `ContextBundle` whose contents are equal to the most recently written bundle. The concrete filenames, JSON shapes, and key-encoding rules are documented in `design.md`.

The store SHALL create the bundle directory if it does not exist. The store SHALL overwrite existing bundle files on write (Phase 1 is full-overwrite only — partial/incremental writes are out of scope).

#### Scenario: Round-trip preserves the bundle

- **WHEN** a `ContextBundle` is written to disk with `ContextStore.write(bundle)`
- **AND** the same store's `read()` is then called
- **THEN** the returned `ContextBundle` SHALL equal the written one for every field, including the `None` entries in `descriptions`

#### Scenario: Bundle directory is created if missing

- **WHEN** `ContextStore(bundle_dir).write(bundle)` is called
- **AND** `bundle_dir` does not yet exist
- **THEN** the directory SHALL be created
- **AND** the four bundle files SHALL be written inside it

#### Scenario: Write is a full overwrite

- **WHEN** `ContextStore.write(bundle_a)` is followed by `ContextStore.write(bundle_b)` against the same bundle directory
- **THEN** `ContextStore.read()` SHALL return a bundle equal to `bundle_b`
- **AND** no data from `bundle_a` SHALL remain readable through the store

### Requirement: Bundle carries a schema version and refuses unknown versions

The system SHALL record a bundle-wide schema version in the meta file. The version SHALL govern the shape of all bundle files together — individual files are not versioned independently. `ContextStore.read()` SHALL raise a dedicated exception when the persisted bundle's schema version is not the one the current code understands. Initial version is `1`.

#### Scenario: Reading a bundle written by the current version succeeds

- **WHEN** a bundle is written and then read by the same code version
- **THEN** `read()` SHALL return the bundle without raising

#### Scenario: Reading a bundle with an unknown schema version raises

- **WHEN** `ContextStore.read()` is called against a bundle whose meta file declares a schema version the current code does not recognise
- **THEN** a dedicated exception SHALL be raised naming the expected and encountered versions
- **AND** the bundle files SHALL NOT be partially returned

### Requirement: Bundle integrity is validated on read

The system SHALL validate cross-file consistency on `ContextStore.read()`. Specifically:

- Every table present in the descriptions file SHALL correspond to a table present in the tables file. Descriptions for tables absent from the tables file SHALL raise a dedicated exception.
- Every table present in the tables file SHALL have a corresponding key in the descriptions file (possibly with value `null`). A missing key SHALL raise a dedicated exception — "never attempted" is NOT a legitimate state in v1.

The exception raised SHALL identify the offending table keys to aid operator debugging. Reading an entirely absent bundle (no files written yet) SHALL NOT raise — the concrete return shape for the missing-bundle case is documented in `design.md`.

#### Scenario: Description for an unknown table raises

- **WHEN** the descriptions file contains a key for `(schema, name)` that is not present in the tables file
- **THEN** `ContextStore.read()` SHALL raise a dedicated integrity exception naming the offending key

#### Scenario: Table without a description entry raises

- **WHEN** the tables file lists `(schema, name)` and the descriptions file has no key for that pair
- **THEN** `ContextStore.read()` SHALL raise a dedicated integrity exception naming the missing key

### Requirement: sonar scan writes an end-to-end bundle

The system SHALL expose a `sonar scan` CLI subcommand that takes a DSN and writes a complete `ContextBundle` to the configured bundle directory. The command SHALL compose the connector, description engine, and relationship mapper into a single pipeline — no step is optional in v1. The DSN SHALL be accepted as a positional argument and additionally as a named `--url` alias. The bundle directory SHALL be configurable via `--bundle-dir`. The CLI SHALL exit with a non-zero status on fatal pipeline failure and print a single error line to stderr; partial descriptions (per-table LLM failures) SHALL NOT be treated as fatal and SHALL be persisted as `null` entries per the bundle contract. The default bundle directory and DSN handling rules are documented in `design.md`.

#### Scenario: Successful scan writes the full bundle

- **WHEN** `sonar scan <valid-dsn>` is invoked against a reachable database
- **THEN** the process SHALL exit `0`
- **AND** the bundle directory SHALL contain the four expected files
- **AND** reading those files back via `ContextStore.read()` SHALL return a `ContextBundle` whose `tables`, `descriptions`, and `relationships` collections are all populated

#### Scenario: Per-table description failures do not fail the scan

- **WHEN** the LLM client fails on exactly one table during a scan of multiple tables
- **THEN** `sonar scan` SHALL exit `0`
- **AND** the written `descriptions.json` SHALL contain a `null` entry for the failing table
- **AND** the other tables' descriptions SHALL be populated

#### Scenario: Unreachable database fails the scan

- **WHEN** `sonar scan <invalid-dsn>` is invoked and the database cannot be reached
- **THEN** the process SHALL exit with a non-zero status
- **AND** a single error line SHALL be printed to stderr
- **AND** no bundle files SHALL be written

### Requirement: Bundle I/O does not log sample or description content

The system SHALL emit one INFO log record on logger `sonar.index` per successful `ContextStore.write(bundle)` and one on successful `ContextStore.read()`. Each record SHALL include integer counts (`tables`, `descriptions_present`, `descriptions_null`, `relationships`). The records SHALL NOT include table descriptions, column descriptions, row sample content, or database credentials.

#### Scenario: Write emits a count-only log record

- **WHEN** `ContextStore.write(bundle)` succeeds
- **THEN** exactly one log record SHALL be emitted on logger `sonar.index` at level `INFO`
- **AND** the record SHALL expose integer `tables`, `descriptions_present`, `descriptions_null`, and `relationships` fields
- **AND** the record SHALL NOT contain description text, row sample content, or the DSN

#### Scenario: Read emits a count-only log record

- **WHEN** `ContextStore.read()` succeeds
- **THEN** exactly one log record SHALL be emitted on logger `sonar.index` at level `INFO`
- **AND** the record SHALL expose the same four integer count fields as the write record

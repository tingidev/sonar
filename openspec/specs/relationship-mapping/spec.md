# relationship-mapping Specification

## Purpose
TBD - created by archiving change relationship-mapping. Update Purpose after archive.
## Requirements
### Requirement: Relationship dataclass and kind enum

The system SHALL expose a frozen `Relationship` dataclass as the sole return shape for relationship mapping. Each instance SHALL carry source and target `(schema, table, column)` triples as strings and a `kind: RelationshipKind` categorising the provenance of the edge. `RelationshipKind` SHALL be an `enum.StrEnum` with lowercase string values, distinguishing edges derived from declared database constraints from edges derived from heuristic inference. The enum values SHALL round-trip through JSON without custom encoders so that downstream consumers (context-index persistence, MCP tool output) can serialise and reconstruct them directly.

#### Scenario: Relationship is immutable

- **WHEN** a `Relationship` instance is constructed
- **THEN** any attempt to assign to one of its fields SHALL raise `dataclasses.FrozenInstanceError`

#### Scenario: RelationshipKind round-trips through JSON

- **WHEN** a `Relationship` is serialised with `json.dumps(..., default=str)` and deserialised with `json.loads`
- **THEN** the `kind` value SHALL appear as either the literal string `"declared"` or `"inferred"`
- **AND** passing that string to `RelationshipKind(value)` SHALL reconstruct the enum member

### Requirement: Declared foreign keys emit one DECLARED relationship per column pair

The system SHALL expose `map_relationships(tables: list[Table], foreign_keys: list[ForeignKey]) -> list[Relationship]`. For every input `ForeignKey`, the returned list SHALL contain exactly one corresponding `Relationship` with `kind=RelationshipKind.DECLARED` and source/target fields copied verbatim. Composite foreign keys, which arrive as one `ForeignKey` per column pair, SHALL produce one `Relationship` per input. Declared relationships SHALL be returned even when the target schema or table is not represented in the `tables` argument.

#### Scenario: Simple declared FK becomes a DECLARED relationship

- **WHEN** `map_relationships` is called with one `ForeignKey(source=("public","orders","user_id"), target=("public","users","user_id"))` and a tables list containing both tables
- **THEN** the result SHALL contain exactly one `Relationship` with `kind=RelationshipKind.DECLARED`
- **AND** its source/target fields SHALL match the `ForeignKey` input verbatim

#### Scenario: Composite declared FK becomes multiple DECLARED relationships

- **WHEN** `map_relationships` is called with two `ForeignKey` entries representing a composite `(a, b)` FK
- **THEN** the result SHALL contain exactly two DECLARED relationships
- **AND** the entry for source column `a` SHALL point to target column `a`
- **AND** the entry for source column `b` SHALL point to target column `b`

#### Scenario: Cross-schema declared FK is preserved when target schema is absent from tables

- **WHEN** `map_relationships` is called with a declared FK whose target `schema` is not represented in the `tables` list
- **THEN** the declared `Relationship` SHALL still appear in the result
- **AND** its target fields SHALL match the `ForeignKey` input verbatim

### Requirement: Columns with a foreign-key-like suffix are inferred to a single same-schema target

The system SHALL infer a relationship for every non-primary-key column whose name matches the single-column primary key of one or more same-schema tables, **via the naming rules documented in `design.md`**, and whose `(source_schema, source_table, source_column)` is NOT already covered by a declared foreign key. The target SHALL be a table in the **same schema** as the source. The candidate target MUST have a single-column primary key. Candidates produced by the configured naming rules SHALL be combined and deduplicated; an `INFERRED` relationship SHALL be emitted if and only if the deduplicated candidate set has exactly one entry. Ambiguous, cross-schema, or ill-formed candidates SHALL NOT produce any edge.

#### Scenario: Column matching a single same-schema PK by name produces one INFERRED edge

- **WHEN** `map_relationships` is called with a non-PK column `public.activities.action_type` and no declared FK on that column
- **AND** the tables list includes exactly one same-schema table `public.action_type` whose single-column PK is also named `action_type`
- **THEN** the result SHALL contain exactly one INFERRED `Relationship` from `public.activities.action_type` to `public.action_type.action_type`

#### Scenario: Role-prefixed column matching a single same-schema PK produces one INFERRED edge

- **WHEN** `map_relationships` is called with a non-PK column `public.metabolism.enzyme_tid` and no declared FK on that column
- **AND** the tables list includes exactly one same-schema table whose single-column PK is named `tid`
- **AND** no same-schema table has a single-column PK named `enzyme_tid`
- **THEN** the result SHALL contain exactly one INFERRED `Relationship` from `public.metabolism.enzyme_tid` to that table's `tid` column

#### Scenario: Ambiguous candidates emit no relationship

- **WHEN** the configured naming rules would match more than one same-schema candidate table (each with a qualifying single-column PK)
- **THEN** NO inferred relationship SHALL be emitted for that column

#### Scenario: Candidate without a qualifying single-column PK emits no relationship

- **WHEN** the only candidate table identified by the naming rules does not have a single-column primary key
- **THEN** NO inferred relationship SHALL be emitted for that column

#### Scenario: Cross-schema candidate emits no relationship

- **WHEN** the source column is in one schema and the only candidate target table is in a different schema
- **THEN** NO inferred relationship SHALL be emitted for that column

#### Scenario: PK source column emits no relationship

- **WHEN** a source column is itself a primary key
- **THEN** NO inferred relationship SHALL be emitted for that column

### Requirement: Catch-all PK columns are excluded as inference targets

The system SHALL exclude a primary-key column from the candidate-target pool of inference — for every naming rule — when its name is over-shared among same-schema tables. The intent is to prevent a single PK from absorbing many semantically unrelated columns (e.g. a `version.name` PK matched by every `*_name` column in the schema). The concrete metric (combined match-pressure across the configured naming rules) and threshold are documented in `design.md`.

#### Scenario: Over-shared PK column does not absorb same-name matches

- **WHEN** a same-schema table has a single-column PK whose name has match-pressure above the design-documented threshold
- **AND** another same-schema table has a non-PK column with the identical name
- **THEN** no INFERRED relationship SHALL target that PK from that column

#### Scenario: Over-shared PK column does not absorb role-prefix matches

- **WHEN** a same-schema table has a single-column PK whose name has match-pressure above the design-documented threshold
- **AND** another same-schema table has a non-PK column whose name ends in `_<pk_name>`
- **THEN** no INFERRED relationship SHALL target that PK from that column

#### Scenario: Under-threshold PK column is still a valid target

- **WHEN** a same-schema table has a single-column PK whose match-pressure is at or below the design-documented threshold
- **THEN** the PK column SHALL remain a valid candidate target under the configured naming rules

### Requirement: Declared relationships block inference on the same source column

The system SHALL NOT emit any INFERRED relationship whose `(source_schema, source_table, source_column)` triple is already the source of a DECLARED relationship. This invariant SHALL hold even when the inference rules would otherwise produce a candidate.

#### Scenario: Declared FK suppresses inference on its source column

- **WHEN** a declared FK exists on `public.orders.user_id`
- **AND** the inference rules would also match the same source column
- **THEN** the result SHALL contain exactly one DECLARED `Relationship` for that source column
- **AND** NO INFERRED `Relationship` SHALL exist with that source column

### Requirement: Result ordering is deterministic

The system SHALL return relationships in a deterministic order: all DECLARED relationships first in their input order, then all INFERRED relationships sorted by `(source_schema, source_table, source_column)`. The function SHALL return an empty list when both `tables` and `foreign_keys` are empty.

#### Scenario: Declared relationships come first in input order

- **WHEN** `map_relationships` is called with three foreign keys in a specific input order
- **AND** any number of inferred relationships would also be produced
- **THEN** the first three entries in the result SHALL be the declared relationships in the same order as the input `foreign_keys`

#### Scenario: Inferred relationships are sorted by source triple

- **WHEN** the result contains multiple INFERRED relationships
- **THEN** they SHALL appear in ascending order of `(source_schema, source_table, source_column)`

#### Scenario: Empty inputs return an empty list

- **WHEN** `map_relationships([], [])` is called
- **THEN** the result SHALL be an empty list

### Requirement: Mapping emits one INFO log record per call

The system SHALL emit exactly one log record per `map_relationships` call on the logger `sonar.relationships` at level `INFO`. The record SHALL include integer counts for declared edges, inferred edges, and tables scanned. The record SHALL NOT include column sample values or row content.

#### Scenario: Log record emitted with counts

- **WHEN** `map_relationships` is called and returns any mix of declared and inferred relationships
- **THEN** exactly one record SHALL be emitted on logger `sonar.relationships` at level `INFO`
- **AND** the record SHALL expose integer `declared`, `inferred`, and `tables_scanned` fields


## MODIFIED Requirements

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

## ADDED Requirements

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

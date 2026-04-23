## ADDED Requirements

### Requirement: Identifiers containing a literal dot are rejected

The connector SHALL raise `ValueError` from `discover_tables` and `discover_relationships` if any returned schema name or table name contains the character `"."`. The exception SHALL name the offending identifier in its message. This invariant exists to keep the `context-index` bundle's on-disk `"<schema>.<name>"` key encoding unambiguous.

#### Scenario: Dotted schema name rejected

- **WHEN** `discover_tables()` is called against a database containing a schema whose name includes `"."`
- **THEN** a `ValueError` SHALL be raised
- **AND** its message SHALL identify the offending schema name
- **AND** no `Table` entries SHALL be returned to the caller

#### Scenario: Dotted table name rejected

- **WHEN** `discover_tables()` is called against a database containing a table whose name includes `"."`
- **THEN** a `ValueError` SHALL be raised
- **AND** its message SHALL identify the offending schema and table name

#### Scenario: Dotted identifier in a foreign-key target rejected

- **WHEN** `discover_relationships()` is called against a database whose foreign-key constraint references a target schema or table containing `"."`
- **THEN** a `ValueError` SHALL be raised
- **AND** its message SHALL identify the offending identifier

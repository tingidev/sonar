## ADDED Requirements

### Requirement: Row Count Discovery

The connector SHALL populate `Table.row_count` for every returned table during `discover_tables()`. The value SHALL be a non-negative integer representing the connector's best available row-count estimate for that table at the time of discovery, or `None` when no usable estimate is available. The concrete source of the estimate, its accuracy bounds, and the conditions under which `None` is returned are documented in `design.md`.

The estimate's freshness and exactness SHALL NOT be guaranteed by the spec — only its presence and non-negativity. Callers (description engine, MCP server, agents) MUST treat `row_count` as a coarse signal for triage, not a metric.

The discovery query SHALL NOT trigger any side effect on the connected database (no implicit `ANALYZE`, no statistics refresh, no table scan beyond what schema introspection already performs).

#### Scenario: Live tables carry a non-negative row count

- **WHEN** `discover_tables()` is called against a database whose tables have been used long enough for the planner to have collected statistics
- **THEN** every returned table SHALL have `row_count` set to a non-negative integer
- **AND** the value SHALL be within an order of magnitude of the table's true row count

#### Scenario: Tables without usable statistics surface as None

- **WHEN** `discover_tables()` is called against a database containing a table that has never been analysed and has no planner statistics available
- **THEN** that table SHALL be returned with `row_count=None`
- **AND** other tables in the same call SHALL still receive populated counts where statistics are available

#### Scenario: Discovery does not mutate database state

- **WHEN** `discover_tables()` is called
- **THEN** no `ANALYZE`, `VACUUM`, or other statistics-refresh statement SHALL be issued by the connector
- **AND** the connection's transaction SHALL remain read-only with respect to user data and catalog statistics

#### Scenario: Empty tables are distinguishable from unknown

- **WHEN** `discover_tables()` is called against a database containing an analysed table with zero rows
- **THEN** the returned table SHALL have `row_count=0`, not `None`

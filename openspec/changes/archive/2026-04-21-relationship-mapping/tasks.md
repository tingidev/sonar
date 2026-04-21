## 1. Module skeleton and data shape

- [x] 1.1 Create `src/sonar/relationships.py`.
- [x] 1.2 Define `RelationshipKind(enum.StrEnum)` with `DECLARED = "declared"` and `INFERRED = "inferred"`.
- [x] 1.3 Define frozen `Relationship` dataclass with source/target `(schema, table, column)` strings and `kind: RelationshipKind`.
- [x] 1.4 Declare the module logger `_LOGGER = logging.getLogger("sonar.relationships")`.
- [x] 1.5 Stub `def map_relationships(tables, foreign_keys) -> list[Relationship]: ...` with imports from `sonar.connectors.postgres` (`Table`, `Column`, `ForeignKey`).

## 2. Declared-edge pass

- [x] 2.1 Convert each input `ForeignKey` into a `Relationship(kind=DECLARED)` preserving source/target fields verbatim and input order.
- [x] 2.2 Build `_declared_source_set: set[tuple[str, str, str]]` from `(source_schema, source_table, source_column)` triples for inference suppression.

## 3. Suffix inference rule

- [x] 3.1 Write `_stem_from_name(col_name) -> str | None` that lowercases and returns the `<stem>` when the name matches `^(.+)_id$`, else `None`.
- [x] 3.2 Build `_tables_by_schema_name: dict[tuple[str, str], Table]` for O(1) candidate lookup within a schema.
- [x] 3.3 Iterate every non-PK column across `tables`; skip if the triple is in `_declared_source_set` or the stem regex doesn't match.
- [x] 3.4 For each matching column, look up candidates at `(source_schema, stem)` and `(source_schema, stem + "s")`; accept only those whose single-column PK is named `id` or `<stem>_id`.
- [x] 3.5 Emit nothing when 0 or 2+ candidates survive. Emit exactly one INFERRED edge when exactly one survives, pointing at that candidate's single PK column.

## 4. Ordering and logging

- [x] 4.1 Keep declared edges in input order; sort inferred edges by `(source_schema, source_table, source_column)`.
- [x] 4.2 Return `declared + inferred` as a single `list[Relationship]`.
- [x] 4.3 Emit one `INFO` log record on `sonar.relationships` with `extra={"declared": N, "inferred": M, "tables_scanned": T}`; no per-edge logging; no column values.
- [x] 4.4 Ensure `Relationship`, `RelationshipKind`, and `map_relationships` are available at the module's top level (no package re-export needed).

## 5. Unit tests — `tests/test_relationships.py`

- [x] 5.1 Add a helper `_table(schema, name, cols_spec)` returning a `Table` given compact tuples `(name, data_type, nullable, is_pk)`.
- [x] 5.2 Add a helper `_fk(src_schema, src_table, src_col, tgt_schema, tgt_table, tgt_col)` returning a `ForeignKey`.
- [x] 5.3 Test `Relationship` is frozen — assigning to a field raises `FrozenInstanceError`.
- [x] 5.4 Test `RelationshipKind` JSON round-trip: `json.dumps(member)` renders lowercase string; `RelationshipKind(value)` reconstructs the member.
- [x] 5.5 Test simple declared FK → one DECLARED `Relationship` with fields verbatim.
- [x] 5.6 Test composite declared FK (two `ForeignKey` entries) → two DECLARED entries with aligned columns.
- [x] 5.7 Test cross-schema declared FK preserved when target schema is absent from `tables`.
- [x] 5.8 Test plural-form inference: `orders.user_id` + `users` with `id` PK → one INFERRED edge at `users.id`.
- [x] 5.9 Test singular-form inference: `orders.user_id` + `user` with `user_id` PK → one INFERRED edge at `user.user_id`.
- [x] 5.10 Test ambiguity: both `user` and `users` present with acceptable PKs → no inferred edge.
- [x] 5.11 Test unacceptable PK: candidate exists but PK is neither `id` nor `<stem>_id` (or is composite) → no inferred edge.
- [x] 5.12 Test cross-schema isolation: `analytics.events.user_id` with only `public.users` present → no inferred edge.
- [x] 5.13 Test declared blocks inference: declared FK on `orders.user_id` present → exactly one DECLARED edge with that source, no INFERRED edge with that source.
- [x] 5.14 Test deterministic ordering: declared in input order, inferred sorted by source triple.
- [x] 5.15 Test empty inputs: `map_relationships([], [])` returns `[]` and emits one log record with all-zero counts.
- [x] 5.16 Test logging: one INFO record on `sonar.relationships` with integer `declared`, `inferred`, `tables_scanned`; no column values appear in any `record.__dict__` string.

## 6. Verify gate

- [x] 6.1 `poetry run pytest` green across the full suite.
- [x] 6.2 Coverage for `src/sonar/relationships.py` is 100%.
- [x] 6.3 `openspec validate relationship-mapping` passes.

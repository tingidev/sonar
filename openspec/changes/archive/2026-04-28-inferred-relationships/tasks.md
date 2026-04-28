## 1. Replace candidate generation in `src/sonar/relationships.py`

- [x] 1.1 Build a `pk_index: dict[(schema, pk_col_name), list[(schema, table_name)]]` from `tables`, including only single-column-PK tables, populated once per `map_relationships` call.
- [x] 1.2 Build a `non_pk_occurrences: dict[(schema, col_name), int]` counting how many same-schema tables have a non-PK column with that name. *(Replaced during apply: see task 1.3 — the metric was upgraded to combined match-pressure when prototyping showed the original framing missed `version.name`-class catch-alls; design.md D3 updated.)*
- [x] 1.3 Implement a `_candidate_targets(table, column, pk_index, excluded_targets)` helper that returns a deduplicated list of `((target_schema, target_table), target_pk_col)` candidates by applying Rule A (direct PK-name match) and Rule B (role-prefix match) per design D1, with the catch-all-PK filter (D3) applied to **both rules** via `excluded_targets` derived from match-pressure.
- [x] 1.4 Replace the existing `_stem_from_name` / `_find_candidates` / `_single_column_pk` flow in `map_relationships` with the new candidate generator. Keep the declared-source-skip, PK-source-skip, single-candidate-only, sort, and log behaviour unchanged.
- [x] 1.5 Define the catch-all PK threshold as a named module-level constant with a comment pointing to `design.md` D3. Final value: 15 (changed from initial 5 when the metric switched to match-pressure).
- [x] 1.6 Remove now-unused `_STEM_PATTERN` regex and any other dead helpers; keep public surface (`map_relationships`, `Relationship`, `RelationshipKind`) unchanged.

## 2. Test coverage in `tests/test_relationships.py`

- [x] 2.1 Add a test for Rule A: non-PK column name equals a single same-schema PK name → exactly one INFERRED edge to that column.
- [x] 2.2 Add a test for Rule B: column ending in `_<pk>` matches a single same-schema PK named `<pk>` → exactly one INFERRED edge.
- [x] 2.3 Add a test for combined-rule different-target ambiguity: a column matched by Rule A to one table and Rule B to a different table emits no edge. *(Reframed during apply: dedup of identical targets is structurally impossible because Rule A requires `col == pk_name` and Rule B requires `col != pk_name`. The real combined-rule invariant is ambiguity-block across rules.)*
- [x] 2.4 Existing `test_ambiguity_emits_no_inferred_edge` continues to cover within-rule ambiguity; `test_combined_rules_different_targets_block_via_ambiguity` covers cross-rule ambiguity.
- [x] 2.5 Add a test for the catch-all-PK filter via direct-pressure: 16 same-schema non-PK columns named `name` push pressure on `(public, name)` PK above threshold; the matching column emits no edge.
- [x] 2.6 Add a test for the under-threshold case: 15 same-schema non-PK columns named `name` keep pressure at the threshold; PK remains a valid Rule A target.
- [x] 2.7 Add a test that the filter ALSO covers Rule B (via role-prefix pressure): 16 columns ending in `_name` push pressure above threshold; no role-prefix edges to `version.name`. *(Reframed during apply: the original task said "Rule B unaffected by filter," which was the wrong shape — D3 redesign makes the filter target-based, applying to both rules.)*
- [x] 2.8 Verify all existing scenarios still pass: declared-FK pass-through, declared-source-skip, PK-source-skip, cross-schema candidate suppression, deterministic ordering, single INFO log per call. Two existing-test fixtures (`test_unacceptable_pk_emits_no_edge` subtest 3 and `test_deterministic_ordering`) updated to remove app-style `id` PK assumptions that are now ambiguity-blocked under the new rules — design intent (the app-style regression) preserved.

## 3. Empirical regression check

- [x] 3.1 ChEMBL with FKs hidden, declared-FK ground truth: **62 hits / 67 emitted / 91 declared = 68.1% recall, 92.5% precision**. Above the 65%-recall and 70%-precision bars. Numbers added to `design.md` D3.

## 4. Verify gate

- [x] 4.1 `poetry run ruff check src tests` clean.
- [x] 4.2 `poetry run pytest` green (186 passed); coverage on `src/sonar/relationships.py` at 100%; overall coverage at 97%.

## 1. Module scaffold and CLI wiring

- [x] 1.1 Create `src/sonar/eval/` package with `__init__.py`, `quality.py`, `relationships.py`, `search.py`, `diff.py`, `descriptions.py`, `_report.py`, `_prompts.py`
- [x] 1.2 Add `sonar eval` subcommand to `cli.py` with `--bundle-dir`, `--json`, and mode flags (`--relationships`, `--search`, `--diff`, `--descriptions`). Default mode is bundle quality. Only one mode per invocation.
- [x] 1.3 Add `PyYAML` dependency to `pyproject.toml`

## 2. Bundle quality report (default mode)

- [x] 2.1 Implement `quality.py`: description coverage, relationship coverage, orphan tables, PII distribution, confidence summary. Input: `ContextBundle`. Output: a `QualityReport` dataclass (or dict).
- [x] 2.2 Implement graph reachability in `quality.py`: build undirected adjacency list from relationships, compute connected components, largest component size, mean reachable tables.
- [x] 2.3 Implement human-readable and JSON formatters for the quality report in `_report.py`.
- [x] 2.4 Wire quality report to CLI: load bundle via `ContextStore.read()`, compute metrics, format output.
- [x] 2.5 Tests for `quality.py`: complete bundle, partial descriptions, orphan tables, disconnected components, empty bundle. Pure unit tests with constructed bundles.

## 3. Relationship evaluation

- [x] 3.1 Implement `relationships.py`: accept tables and declared FKs, run `map_relationships(tables, [])`, compare inferred vs declared. Compute recall, precision, F1. Return per-table breakdown, missed list, false-positive list.
- [x] 3.2 Implement human-readable and JSON formatters for relationship report in `_report.py`.
- [x] 3.3 Wire `--relationships <dsn>` to CLI: dispatch to correct connector, discover tables and FKs, run evaluation, format output.
- [x] 3.4 Tests for `relationships.py`: perfect inference, partial inference, no declared FKs, no inferred edges. Pure unit tests with constructed tables and FKs.

## 4. Search relevance

- [x] 4.1 Implement ground-truth YAML loading in `search.py`: parse file, validate schema (list of queries with `query` and `expected` fields), return structured data.
- [x] 4.2 Implement search relevance metrics in `search.py`: for each query, run `search_tool` logic against bundle, compute precision@k, recall@k, reciprocal rank. Aggregate MRR.
- [x] 4.3 Curate ChEMBL search ground-truth YAML file (20-30 queries covering molecules, targets, assays, activities, mechanisms, clinical candidates).
- [x] 4.4 Implement human-readable and JSON formatters for search relevance report in `_report.py`.
- [x] 4.5 Wire `--search <path>` to CLI: load bundle, load ground truth, run evaluation, format output.
- [x] 4.6 Tests for `search.py`: all expected found, partial matches, empty results, invalid YAML. Unit tests with constructed bundles and inline ground-truth dicts.

## 5. Bundle diff

- [x] 5.1 Implement `diff.py`: load two bundles, compare tables (added/removed), relationships (added/removed by kind), descriptions (null transitions, confidence deltas, text changed flag). Return a `DiffReport` structure.
- [x] 5.2 Implement human-readable and JSON formatters for diff report in `_report.py`.
- [x] 5.3 Wire `--diff <other-bundle-dir>` to CLI: load both bundles, run diff, format output.
- [x] 5.4 Tests for `diff.py`: identical bundles, added/removed tables, confidence changes, null-to-present descriptions, relationship additions. Pure unit tests with constructed bundles.

## 6. Description quality (LLM-as-judge)

- [x] 6.1 Write judge scoring prompt in `_prompts.py`: rubric for accuracy, completeness, specificity. Structured JSON output (three floats per table).
- [x] 6.2 Implement `descriptions.py`: for each non-null description, send schema + description to judge via `LLMClient.generate()`, parse scores, aggregate means, flag low scorers.
- [x] 6.3 Implement human-readable and JSON formatters for description quality report in `_report.py`.
- [x] 6.4 Wire `--descriptions` to CLI: load bundle, instantiate LLM client, run evaluation, format output.
- [x] 6.5 Tests for `descriptions.py`: high scores, low scores flagged, null descriptions skipped, judge parse failure handled. Uses `FakeLLMClient` pattern.

## 7. Final integration

- [x] 7.1 Run full test suite, verify 80%+ coverage on `src/sonar/eval/`.
- [x] 7.2 Verify `sonar eval` runs against the ChEMBL `.sonar/` bundle (if available) or a test fixture bundle. Smoke-test all five modes.

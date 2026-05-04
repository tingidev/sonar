## Why

Sonar produces context artifacts (descriptions, relationships, search results) but has no systematic way to measure their quality. The ChEMBL relationship evaluation (68.1% recall, 92.5% precision) was ad-hoc code in an explore session. Description quality and search relevance are completely unmeasured. Operators scanning a new database have no signal for "how complete is my context?" without manually inspecting the bundle. As the pipeline gains capabilities (new connectors, richer inference), regression detection becomes essential — a prompt change or model upgrade can silently degrade output quality with no feedback loop.

## What Changes

- New `sonar eval` CLI subcommand with five evaluation modes:
  - **Bundle quality report** (default, no args): coverage metrics, graph reachability, confidence distribution, PII classification summary. Runs against any `.sonar/` bundle with no external dependencies.
  - **Relationship evaluation** (`--relationships <dsn>`): recall, precision, F1 of inferred relationships against declared FKs from a live database. Per-table breakdown, list of missed and false-positive edges.
  - **Search relevance** (`--search <ground-truth.yaml>`): precision@k, recall@k, MRR against curated query-to-expected-tables pairs. Ships with a ChEMBL ground-truth file.
  - **Bundle diff** (`--diff <bundle-a> <bundle-b>`): structural comparison of two bundles — tables added/removed, relationships changed, description text and confidence deltas.
  - **Description quality** (`--descriptions`): LLM-as-judge scoring of each description on accuracy, completeness, and specificity. Structured per-table scores, not prose. Advisory signal — implemented last, treated as non-gating.
- New `src/sonar/eval/` module tree housing evaluation logic.
- ChEMBL search ground-truth YAML file for search relevance evaluation.

## Capabilities

### New Capabilities
- `evaluation-toolkit`: evaluation commands, metrics computation, ground-truth formats, and reporting for the five evaluation modes.

### Modified Capabilities
None. The `sonar eval` CLI surface is fully owned by the new `evaluation-toolkit` capability. No existing requirements on `context-index`, `mcp-server`, or other capabilities change.

## Impact

- **Code:** new `src/sonar/eval/` package, new CLI subcommand in `cli.py`, new test files under `tests/`.
- **Dependencies:** `PyYAML` for ground-truth file parsing (new dependency). No other new deps — LLM-as-judge reuses the existing `engine.llm` client.
- **APIs/formats:** no changes to existing bundle format, MCP tools, or connector interfaces. The ground-truth YAML format is new and internal to the evaluation toolkit.

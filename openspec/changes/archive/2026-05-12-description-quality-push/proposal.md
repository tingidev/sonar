## Why

Sonar's description engine produces semantic descriptions that are the core value proposition -- they turn raw schemas into agent-consumable context. Today those descriptions have never been stress-tested against messy real-world databases. The single system prompt was written for clean schemas (ChEMBL) and may produce vague, inaccurate, or generic output on databases with heavy abbreviations (FAERS), wide fact tables (TPC-DS), multi-schema layouts (AdventureWorks), mixed naming conventions (Lahman), or all-caps coded columns (CMS SynPUF). Without measurement, prompt improvements are guesswork.

This change builds the measurement infrastructure (cross-provider LLM-as-judge eval with versioned artifacts), establishes baselines across six databases, then iterates on the description engine's prompts and sampling strategy until all databases clear quality thresholds.

## What Changes

- Extend `sonar eval --descriptions` to support cross-provider judging via a `--judge-model` flag, replacing the existing same-model judge pattern
- Replace the three existing judge dimensions (accuracy, completeness, specificity) with three refined dimensions: accuracy (correct reflection of content), specificity (useful detail, not filler), domain inference (correct domain identification and terminology)
- Add per-dimension reasoning output from the judge for debuggability
- Add versioned eval artifact output: JSON files recording scores, judge reasoning, prompt version, generator + judge model versions, and sample tables
- Add a fixed 10-table sample selection mode for iteration loops alongside full-database eval
- Build Docker fixtures and seed data for five new databases (FAERS, TPC-DS, AdventureWorks, Lahman, CMS SynPUF via DuckDB)
- Iterate on `_prompts.py` system prompt and `build_table_prompt` to improve description quality across all six databases
- Improve sample row selection strategy to give the LLM more representative data

## Capabilities

### New Capabilities

- `eval-descriptions`: Cross-provider LLM-as-judge evaluation with versioned artifacts, refined dimensions, and fixed-sample iteration mode

### Modified Capabilities

- `evaluation-toolkit`: The `--descriptions` mode gains `--judge-model` flag and writes versioned artifact JSON alongside console output
- `description-engine`: Prompt and sampling strategy changes to improve description quality across diverse schemas

## Impact

- `src/sonar/eval/descriptions.py` — refactored for cross-provider judge, new dimensions, reasoning capture
- `src/sonar/eval/_prompts.py` — new judge prompt with refined dimensions and reasoning requirement
- `src/sonar/eval/_report.py` — updated report formatters for new dimensions
- `src/sonar/eval/_types.py` — updated score dataclasses
- `src/sonar/engine/_prompts.py` — improved system prompt and table prompt
- `src/sonar/cli.py` — new `--judge-model` flag, artifact output path, sample selection
- `tests/fixtures/` — five new database fixtures (Dockerfiles and seed scripts already staged)
- `docker-compose.yml` — already staged with five new services
- `tests/` — new tests for eval changes, updated tests for prompt changes

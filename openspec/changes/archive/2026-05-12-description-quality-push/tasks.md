## 1. Database fixtures

- [x] 1.1 Validate and complete FAERS fixture (Dockerfile, load script, verify 7 tables load and are queryable at port 5435)
- [x] 1.2 Validate and complete TPC-DS fixture (Dockerfile, generate script, verify 24 tables load at port 5436)
- [x] 1.3 Validate and complete AdventureWorks fixture (Dockerfile, load script, verify 68 tables across 5 schemas at port 5437)
- [x] 1.4 Validate and complete Lahman fixture (Dockerfile, load script, verify 27 tables at port 5438)
- [x] 1.5 Validate and complete CMS SynPUF fixture (setup.sh for DuckDB, verify 5 tables with Parquet loading)

## 2. Eval scoring refactor

- [x] 2.1 Replace `TableScore` dataclass: integer 1-5 fields for accuracy, specificity, domain_inference; add reasoning strings per dimension
- [x] 2.2 Rewrite judge prompt in `eval/_prompts.py`: 1-5 rubric anchors, three refined dimensions, require reasoning per dimension in JSON output
- [x] 2.3 Update `_parse_score` to handle new JSON shape (integer scores, reasoning strings, clamping to 1-5)
- [x] 2.4 Update `evaluate_descriptions` flagging threshold from 0.5 float to <3 integer
- [x] 2.5 Update report formatters in `_report.py` for new dimensions and integer scale
- [x] 2.6 Update existing description eval tests and add tests for new scoring shape, clamping, and flagging

## 3. Cross-provider judge and CLI flags

- [x] 3.1 Add `--judge-model` flag to `sonar eval --descriptions` CLI, create separate judge LLM client when specified
- [x] 3.2 Add `--sample N` flag: select N tables by round-robin across schemas (sort within schema, interleave by sorted schema order), pass subset to `evaluate_descriptions`
- [x] 3.3 Add `--output` flag: write versioned JSON artifact with run metadata, scores, reasoning, and prompt version hash
- [x] 3.4 Compute prompt version hash (SHA-256 of system prompt + table prompt template) and include in artifact
- [x] 3.5 Add tests for --judge-model routing, --sample selection, --output artifact writing, and prompt hash stability

## 4. Baseline and prompt iteration

- [x] 4.1 Run baseline eval across all six databases with current prompts, record artifacts
- [x] 4.2 Analyze baseline results: identify weak patterns from judge reasoning across databases; compute Pearson correlation between accuracy and domain-inference per database (open question from design.md — merge dimensions if correlation >= 0.85 across all six); spot-check FAERS and CMS SynPUF for the D8 trade-off (judge penalizing sample-driven inferences with "not supported by schema" comments on correct descriptions)
- [x] 4.3 Improve system prompt in `_prompts.py`: add instructions for abbreviation expansion, domain inference, and specificity
- [x] 4.4 Improve `build_table_prompt`: enhance column and sample presentation for messy schemas
- [x] 4.5 Re-evaluate after prompt changes, compare against baseline artifacts
- [x] 4.6 Iterate prompt improvements until all six databases clear quality thresholds (thresholds set in 4.1)
- [x] 4.7 Update tests for any changed prompt assertions

# Sonar

Open-source data context layer for AI agents. Connects to data sources, auto-discovers schemas, generates semantic descriptions via LLM, exposes context through MCP.

## Spec-driven development

All feature work goes through OpenSpec. No production code without an accepted spec.

### Artifact layout

- **Specs (source of truth):** `openspec/specs/<capability>/spec.md` — accumulated requirements per capability.
- **Changes (in flight):** `openspec/changes/<name>/` — `proposal.md`, `specs/<capability>/spec.md` (delta), `design.md` (if architectural), `tasks.md`.
- **Archive:** `openspec/changes/archive/YYYY-MM-DD-<name>/` — completed changes whose deltas have been merged into `openspec/specs/`.

Planned changes live in `ROADMAP.md`. One change in flight at a time.

### Per-feature lifecycle

Each feature goes from idea to tested implementation via five skills plus two manual gates. Do not skip or overlap phases.

```
/opsx:explore  →  /opsx:propose  →  /opsx:apply  →  /opsx:audit  →  /opsx:archive
  (think)         (artifacts)       (build+test)     (review)         (merge+move)
```

1. **`/opsx:explore <topic>`** — optional thinking phase. Read-only stance: discuss, diagram, compare options, investigate the codebase. May edit OpenSpec artifacts if the user asks; must not write application code. Skip when the idea is already crisp.

2. **`/opsx:propose <name>`** — scaffolds `openspec/changes/<name>/` and generates artifacts in dependency order via `openspec instructions`:
   - `proposal.md` (what and why)
   - `specs/<capability>/spec.md` (delta: requirements with WHEN/THEN scenarios)
   - `design.md` (architectural decisions, when the change warrants it)
   - `tasks.md` (ordered, checkbox implementation steps)

   Review and edit the artifacts before moving on. `openspec validate <name>` must pass.

3. **`/opsx:apply <name>`** — reads the artifacts, works `tasks.md` top-to-bottom, flips `- [ ]` → `- [x]` as each task completes. Writes tests alongside code. Pauses (does not guess) when a task is ambiguous, a blocker appears, or implementation reveals a design issue — in which case update `design.md` / spec delta first, then resume.

4. **Verify** — `poetry run pytest` passes and coverage stays above 80%. Manual gate, not a skill.

5. **`/opsx:audit <name>`** — independent reviewer pass on the in-flight change. Two lenses only: **security at boundaries** (secrets, input validation, injection surface, logging discipline) and **spec-code fit** (does the implementation match what `specs/<cap>/spec.md` actually says?). Style/quality/correctness lenses are skipped — ruff + pytest + coverage already cover that territory.

   **Advisory, not gating.** Findings come with severity (low/medium/high/critical) *and* confidence (low/medium/high). Address `high`-severity findings with `high` confidence before archive; lower-confidence findings are triaged and either fixed, deferred (written as dated TODOs on the relevant design.md Open Questions), or dismissed. One pass only — no re-review loops. Reviewer agents have a known availability bias (finding issues is their job), so "clean bill + short report" is a valid outcome; pressure for more findings is how theatre starts.

6. **`/opsx:archive <name>`** — checks that all artifacts and tasks are done (warns if not), merges `openspec/changes/<name>/specs/<cap>/spec.md` into the accumulated `openspec/specs/<cap>/spec.md`, and moves the change directory to `openspec/changes/archive/YYYY-MM-DD-<name>/`.

7. **Post-archive** — add a `LEARNINGS.md` section for this change (see below), then propose the next roadmap change.

**Cross-cutting audit** — separately, every few changes, run an ad-hoc reviewer pass over the whole shipped codebase (not a per-change scope). Consistency drift and abstraction mismatch across capabilities only become visible at this granularity — the per-change audit can't catch them. Invoke manually via the `reviewer` subagent with a cross-cutting prompt; not a slash command.

### Skill-free fallback

Four of the five slash commands wrap the `openspec` CLI. `/opsx:audit` instead invokes the `reviewer` subagent with a pinned scope (security-at-boundaries + spec-code fit) — no `openspec` CLI equivalent. When learning or when a skill misbehaves, the rest of the workflow runs manually: `openspec new change <name>`, `openspec status --change <name> --json`, `openspec instructions <artifact-id> --change <name> --json`, `openspec validate <name>`, and a plain `mv` into `openspec/changes/archive/`.

### Freeze discipline

Spec-driven development makes commitments permanent-feeling. Apply these rules when drafting spec deltas and design decisions so we lock only what needs locking, and leave room to update the rest when real consumers pull on it.

- **Spec at shape level, not value level.** A `spec.md` requirement names a field, its purpose, and the behaviour it guarantees — not the concrete enum values, thresholds, or taxonomy. Lock a value in the spec only when a downstream consumer will parse it directly *and* changing it later is costly. Concrete values live in `design.md`.
- **Every `design.md` decision ends with two lines:**
  - `Revisit when: <concrete trigger>` — a specific event (first real-schema test, first external consumer, first missing taxonomy value), not "if it doesn't work."
  - `Reversibility: cheap | expensive` — cheap reversals are free to freeze. Expensive reversals (public APIs, persisted file formats, MCP tool signatures, anything external consumers will depend on) must cite evidence (an explore artifact, a real-data spike, a prior-art reference) rather than intuition.
- **Minimum interface for the next consumer.** Before adding a field, requirement, or capability split, name the concrete next change that will pull on it. If no next change exists, defer it. Grow the interface additively when downstream demand appears.
- **Flag speculative splits.** Capability splits, abstractions, or indirection layers added for hypothetical future needs (e.g. "for a possible LiteLLM swap") are marked `Speculative: yes` in `design.md` and default to the simpler path until a concrete consumer demands otherwise.

The point of the two-line tail on design decisions is to make review skimmable: the user scans `Revisit when` and `Reversibility` across decisions rather than re-deriving each one. Anything marked `expensive` without evidence is the review surface.

## Project layout

```
src/sonar/
  connectors/   # Data source adapters
  engine/       # LLM + description generation
  index/        # Context storage
  mcp/          # Agent interface
  cli.py        # CLI entrypoint
tests/
openspec/       # Spec-driven artifacts
```

## Commands

```bash
poetry install          # Install dependencies
poetry run pytest       # Run tests
poetry run sonar scan   # Discover + describe a database
poetry run sonar serve  # Start MCP server
```

## Conventions

- Python 3.11+, Poetry, async throughout (psycopg3, MCP).
- Immutable data structures — frozen dataclasses, no mutation.
- Error handling at boundaries only (DB connections, LLM API). Trust internal code.
- Ruff line-length 100.
- Tests: pytest, pytest-asyncio auto mode, 80% coverage target.
- No emojis in code, comments, or docs.

## LEARNINGS.md

After each archived change, add a section to `LEARNINGS.md` explaining the non-obvious parts of what was built — as if teaching a junior SWE. Focus on design rationale, protocol expectations, how pieces connect. Not what the code obviously does.

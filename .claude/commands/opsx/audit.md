---
name: "OPSX: Audit"
description: Independent reviewer pass on an in-flight change — security + spec-code fit only, advisory
category: Workflow
tags: [workflow, review, experimental]
---

Independent reviewer pass on an in-flight change. Runs between verify (pytest green) and archive. Two lenses only: security at boundaries and spec-code fit. Advisory — findings come with severity and confidence; user triages.

---

**Input**: The argument after `/opsx:audit` is the change name (kebab-case). If omitted, check if inferrable from conversation context; otherwise list active changes via `openspec list --json` and use the AskUserQuestion tool to prompt for selection. Do NOT guess.

**Preconditions**

- Verify gate has passed: `poetry run pytest` is green and coverage is above 80%. If you cannot confirm this from conversation context, run it first.
- The change exists at `openspec/changes/<name>/` (not yet archived).

**Steps**

1. **Gather scope**

   - Read `openspec/changes/<name>/proposal.md`, `design.md` (if present), and every file under `openspec/changes/<name>/specs/<cap>/spec.md`.
   - Identify the modified/new capabilities from the delta specs.
   - Read the corresponding accumulated spec(s) at `openspec/specs/<cap>/spec.md` for the pre-change baseline.
   - Identify the changed source files. Prefer `git diff --name-only <base>...HEAD` scoped to `src/` and `tests/`; if that's not available, read `tasks.md` and extract the file paths it names.

2. **Invoke the `reviewer` subagent**

   Use the `Agent` tool with `subagent_type: "reviewer"`. Hand it a prompt with:

   - **Change name and scope**: the change under audit, the modified capabilities, and the concrete file lists (changed source files, changed test files, delta spec files, accumulated spec files).
   - **Two lenses, explicit**:
     - **Lens 1 — Security at boundaries.** No hardcoded secrets. Env-var discipline for API keys. Injection surface at external boundaries (SQL via `psycopg.sql.Identifier`/`Literal`; any shell/subprocess; any deserialisation of untrusted data). Input validation at system boundaries only (not internal). Logging discipline: no PII, no row content, no prompt/response content.
     - **Lens 2 — Spec-code fit.** For each requirement in the delta spec, does the implementation honour it? Any behaviour the spec promises that the code doesn't deliver? Any behaviour in the code that isn't in the spec (scope creep)? Cross-reference the WHEN/THEN scenarios against test cases.
   - **Explicit skip list**: style/formatting (ruff handles), coverage as a number (pytest-cov enforces), docstring completeness, micro-optimisations, hypothetical future consumers, speculative refactors without a concrete failure.
   - **Output format**: per-finding severity (low/medium/high/critical) AND confidence (low/medium/high); affected files; concrete recommendation. Clean-bill items listed so the user knows what was verified.
   - **Availability-bias disclaimer**: a short report on a clean change is the correct outcome; padding with low-confidence findings to look thorough is a known failure mode. Report what's real.

3. **Surface the report to the user**

   Print the reviewer's full report back to the user. Do not re-summarise, do not editorialise — the user needs to see severity/confidence pairs directly to triage.

4. **Triage prompt**

   After the report, ask the user — open-ended — which findings to address now, which to defer (and where to park them: `design.md` Open Questions, `WORKLOG.md`, or a new change), and which to dismiss. Do NOT auto-fix. Do NOT start a re-review loop.

**Output**

After triage:

- If fixes are taken: summarise what's being fixed in-change vs deferred vs dismissed. The user will apply fixes via direct edits (or the `builder` subagent if they ask); after fixes land, re-run pytest, but **do not re-invoke `/opsx:audit`** — one pass per change.
- If nothing blocks archive: tell the user `/opsx:archive <name>` is clear to run.

**Guardrails**

- One pass only. If the user asks for a re-review after fixes, politely decline and point back to this rule — re-review is how theatre starts.
- Do not widen the scope beyond security-at-boundaries + spec-code fit. Style/quality/correctness findings are out of scope by design.
- Do not block archive. The audit is advisory. The user decides what `high` really means in context.
- If the reviewer reports a `critical` finding with `high` confidence, still surface it and let the user decide — but call it out clearly so it isn't missed.
- Reviewer subagent runs with the `Agent` tool, not as a skill invocation.

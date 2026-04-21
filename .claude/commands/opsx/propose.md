---
name: "OPSX: Propose"
description: Propose a new change - create it and generate all artifacts in one step
category: Workflow
tags: [workflow, artifacts, experimental]
---

Propose a new change - create the change and generate all artifacts in one step.

I'll create a change with artifacts:
- proposal.md (what & why)
- design.md (how)
- tasks.md (implementation steps)

When ready to implement, run /opsx:apply

---

**Input**: The argument after `/opsx:propose` is the change name (kebab-case), OR a description of what the user wants to build.

**Steps**

1. **If no input provided, ask what they want to build**

   Use the **AskUserQuestion tool** (open-ended, no preset options) to ask:
   > "What change do you want to work on? Describe what you want to build or fix."

   From their description, derive a kebab-case name (e.g., "add user authentication" → `add-user-auth`).

   **IMPORTANT**: Do NOT proceed without understanding what the user wants to build.

2. **Create the change directory**
   ```bash
   openspec new change "<name>"
   ```
   This creates a scaffolded change at `openspec/changes/<name>/` with `.openspec.yaml`.

3. **Get the artifact build order**
   ```bash
   openspec status --change "<name>" --json
   ```
   Parse the JSON to get:
   - `applyRequires`: array of artifact IDs needed before implementation (e.g., `["tasks"]`)
   - `artifacts`: list of all artifacts with their status and dependencies

4. **Create artifacts in sequence until apply-ready**

   Use the **TodoWrite tool** to track progress through the artifacts.

   Loop through artifacts in dependency order (artifacts with no pending dependencies first):

   a. **For each artifact that is `ready` (dependencies satisfied)**:
      - Get instructions:
        ```bash
        openspec instructions <artifact-id> --change "<name>" --json
        ```
      - The instructions JSON includes:
        - `context`: Project background (constraints for you - do NOT include in output)
        - `rules`: Artifact-specific rules (constraints for you - do NOT include in output)
        - `template`: The structure to use for your output file
        - `instruction`: Schema-specific guidance for this artifact type
        - `outputPath`: Where to write the artifact
        - `dependencies`: Completed artifacts to read for context
      - Read any completed dependency files for context
      - Create the artifact file using `template` as the structure
      - Apply `context` and `rules` as constraints - but do NOT copy them into the file
      - Show brief progress: "Created <artifact-id>"

   b. **Continue until all `applyRequires` artifacts are complete**
      - After creating each artifact, re-run `openspec status --change "<name>" --json`
      - Check if every artifact ID in `applyRequires` has `status: "done"` in the artifacts array
      - Stop when all `applyRequires` artifacts are done

   c. **If an artifact requires user input** (unclear context):
      - Use **AskUserQuestion tool** to clarify
      - Then continue with creation

5. **Show final status**
   ```bash
   openspec status --change "<name>"
   ```

**Output**

After completing all artifacts, summarize:
- Change name and location
- List of artifacts created with brief descriptions
- What's ready: "All artifacts created! Ready for implementation."
- Prompt: "Run `/opsx:apply` to start implementing."

**Artifact Creation Guidelines**

- Follow the `instruction` field from `openspec instructions` for each artifact type
- The schema defines what each artifact should contain - follow it
- Read dependency artifacts for context before creating new ones
- Use `template` as the structure for your output file - fill in its sections
- **IMPORTANT**: `context` and `rules` are constraints for YOU, not content for the file
  - Do NOT copy `<context>`, `<rules>`, `<project_context>` blocks into the artifact
  - These guide what you write, but should never appear in the output

**Freeze Discipline (apply to spec.md deltas and design.md)**

The project's root `CLAUDE.md` defines freeze discipline for this repo. Apply it when drafting — it constrains *what* goes into each artifact, on top of the schema's structural rules:

- **spec.md deltas — write at shape level, not value level.** Requirements name the field, its purpose, and the behaviour guaranteed. Concrete enum values, thresholds, or taxonomies go in `design.md`, not the spec. Only lock a value in the spec if a downstream consumer will parse it directly *and* changing it later is costly (e.g. a persisted file format, an MCP tool signature consumers depend on).

- **design.md — every numbered decision (D1, D2, ...) ends with two lines:**
  ```
  Revisit when: <concrete trigger>
  Reversibility: cheap | expensive
  ```
  - `Revisit when` must name a specific event (e.g. "first real-schema test shows mismatch", "first external MCP consumer asks for a missing type"). Avoid vague triggers like "if it doesn't work."
  - `Reversibility: expensive` decisions must cite evidence — an explore artifact, a real-data spike, or a prior-art reference. Not intuition. If no evidence exists yet, either run `/opsx:explore` first or default to the simpler reversible path.

- **Minimum interface for the next consumer.** Before adding a field, requirement, or capability split to this change, name the concrete *next* change (from ROADMAP.md or the in-flight queue) that will pull on it. If you cannot, defer the addition. Grow interfaces additively when downstream demand appears — do not pre-build for imagined consumers.

- **Flag speculative abstractions.** Capability splits, indirection layers, or extension points introduced for hypothetical future needs (e.g. "for a possible LiteLLM swap", "in case we add a second provider later") get an explicit `Speculative: yes` line on the relevant design decision. Default to the simpler single-path design unless a concrete consumer demands the split now.

These tails are the user's review surface. When the user reviews the proposal, they skim `Revisit when` + `Reversibility` across design decisions rather than re-deriving each one. Missing tails, vague triggers, or `expensive` reversibility without evidence are the cue to pause and ask.

**Guardrails**
- Create ALL artifacts needed for implementation (as defined by schema's `apply.requires`)
- Always read dependency artifacts before creating a new one
- If context is critically unclear, ask the user - but prefer making reasonable decisions to keep momentum
- If a change with that name already exists, ask if user wants to continue it or create a new one
- Verify each artifact file exists after writing before proceeding to next

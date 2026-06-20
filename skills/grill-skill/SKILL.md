---
name: grill-skill
description: Use when the user wants to create caliper evals for a skill, iterate on a skill using evals, or run the full create → test → improve cycle for a skill they want to measure.
allowed-tools: Bash, Read, Write, Edit
---

# Grill Skill

A guided workflow for creating caliper evals and iterating on a skill. Interviews the user to generate well-structured eval tasks, runs them with increasing rigor, and loops until the skill is ready to ship.

## Prerequisites

`caliper` must be installed. If missing:

```bash
pipx install caliper-eval
```

## Entry point

The user invokes `/grill-skill [path]` with an optional path to a `SKILL.md`.

- **Path provided** — use it directly.
- **No path** — look for `SKILL.md` in the current working directory. If found, tell the user what you found and confirm before proceeding. If not found, ask the user where their skill file is.

## Phase 1: Understand the skill

Read the `SKILL.md`. Then give the user a 2–3 sentence summary covering:
- What the skill does
- When it triggers
- What a successful run looks like (your interpretation)

Ask: "Does this match your understanding, or should I adjust my reading?"

Wait for confirmation before continuing.

## Phase 2: Detect eval mode

Look for any `*.eval.yaml` file in the same directory as the `SKILL.md`. Try the canonical name first (`<dir-name>.eval.yaml`) then any other `*.eval.yaml`.

- **None found** → [New eval flow](#new-eval-flow)
- **Found** → [Gap-fill flow](#gap-fill-flow)

---

## New eval flow

Generate exactly 3 tasks: happy path, edge case, and adversarial. Ask one question at a time and wait for the answer before asking the next.

### Task 1 — Happy path

Ask: "What's the most common way someone uses this skill? Describe what a successful run looks like — what did the agent do, and what would you check to confirm it worked?"

Turn the answer into a task with:
- A realistic `prompt`
- A clear `expect:` criterion describing observable success
- An `assert:` block if success is verifiable via files, command output, or git state

### Task 2 — Edge case

Ask: "What's a tricky or unusual input this skill should still handle correctly — something that might trip up the raw agent without the skill?"

Turn the answer into a task. Include `assert:` where the outcome is checkable.

### Task 3 — Adversarial

Ask: "What should this skill *not* do, or refuse to do? What's a request where you'd want the agent to push back, warn the user, or avoid a risky action?"

Turn the answer into a task. The `expect:` should describe the desired refusal or safe behavior.

### Confirm and write

After all three questions, show the proposed tasks in YAML and ask: "Does this look right before I write the file?"

Derive the spec filename from the directory name of the skill file (e.g., `skills/my-skill/SKILL.md` → `skills/my-skill/my-skill.eval.yaml`). Use `claude-code` as the default backend for both `skill` and `judge` unless the user's `SKILL.md` targets a different backend.

Write the spec to the same directory as `SKILL.md`.

---

## Gap-fill flow

Read the existing `.eval.yaml`. Summarize existing tasks:

> "I found [N] existing tasks:
> 1. [name] — [one-line summary]
> 2. ...
>
> What behaviors do you feel are missing or under-tested?"

Always ask what behaviors are missing or under-tested before proposing new
tasks. If the user asks you only to inspect or report the existing eval, do not
modify the file; report the existing tasks and still end by asking what is
missing or under-tested.

Interview the user to surface gaps. For each gap, ask a follow-up to sharpen it into a concrete task (prompt + expect + optional assert). Show the proposed additions and confirm before writing them into the spec.

---

## Phase 3: First run

Validate the spec and run with `k=1`:

```bash
caliper validate <spec-path>
caliper run <spec-path> --k 1
```

Show the results. If the run exits with a harness or config error (not a task failure), diagnose and fix it before asking the user what to do next.

## Phase 4: Iteration loop

After showing results, ask:

> "Want to keep iterating on the skill, or are you done?
> - **Iterate** — edit your SKILL.md, then let me know and I'll re-run with k=3.
> - **Done** — I'll suggest a baseline run before you commit."

### If iterating

Wait for the user to confirm they've made their edits. Then run:

```bash
caliper run <spec-path> --k 3
```

Show the results. Loop back to the same question.

### If done

Suggest a baseline run before committing:

> "Before committing, it's worth running with `--baseline` to confirm the skill is making a real difference:
>
> ```bash
> caliper run <spec-path> --k 3 --baseline
> ```
>
> This shows the delta between with-skill and without-skill scores."

After the baseline run (or if the user skips it), remind them to commit:

> "Commit both `SKILL.md` and `<spec-name>.eval.yaml` together so contributors can run the same eval."

---
name: grill-skill
description: Build and harden a skill with evals — interview to design its eval tasks, then run, measure, and iterate. Use when the user wants to create or improve a skill's eval, or run the create → test → improve loop for a skill.
allowed-tools: Bash, Read, Write, Edit
---

# Grill Skill

Interview the user to design a skill's eval, then loop run → measure → improve until it ships. Requires `caliper` (`pipx install caliper-eval` if missing). Commands, spec skeleton, and expect/assert guidance: [REFERENCE.md](REFERENCE.md).

## Entry point

`/grill-skill [path]` — optional path to a `SKILL.md`.

- **Path given** — use it.
- **No path** — look for `SKILL.md` in the cwd; if found, confirm before proceeding, else ask where it is.

## Phase 1 — Understand

Read the `SKILL.md`. Summarize what it does, when it triggers, and what a successful run looks like. Ask the user to confirm your reading. **Wait for confirmation before continuing.**

## Phase 2 — Detect eval mode

Look for `*.eval.yaml` beside the `SKILL.md` (try `<dir-name>.eval.yaml` first).

- **None** → New eval. **Found** → Gap-fill.

Interview one question at a time and wait for each answer. Never invent the user's answers or write the spec before interviewing.

### New eval — three tasks

Elicit three tasks, one question at a time:

1. **Happy path** — the most common successful use. What did the agent do, and what would confirm it worked?
2. **Edge case** — a tricky-but-valid input that might trip the raw agent.
3. **Adversarial** — what the skill should refuse or avoid.

Turn each answer into a task: a realistic `prompt`, an observable `expect`, and an `assert` when the outcome is checkable (see [REFERENCE.md](REFERENCE.md)). Show the proposed YAML and confirm before writing.

Write the spec beside `SKILL.md`, named `<dir-name>.eval.yaml`, with `skill.path: ./SKILL.md` and `claude-code` as the default backend for both `skill` and `judge` unless the SKILL.md targets another.

### Gap-fill

Read the existing spec and report its tasks. **Ask what behaviors are missing or under-tested before proposing or writing anything** — even if the user only asked you to inspect it, report first, then ask. Sharpen each gap into a task, show it, and confirm before writing it in.

## Whose setup is measured

By default every run **loads the user's own customizations**: the servers their CLI is configured with and their account's hosted connectors, merged with the spec's `mcp:`. That answers "does my skill work in *my* agent?", which is what most users want. Isolating a run (`--no-user-customizations`, or `user_customizations: false` in the spec) gives the attempt only the declared servers, so the score is portable.

**Keep the default** when:
- the user is testing or iterating on their own skill in their own agent;
- ablating one of the spec's skills or servers (`--ablate`): both runs load the same setup, so the difference is still the removed subject.

**Isolate** when:
1. comparing backends or models (`--model claude-code` vs `--model codex`): each CLI loads a different setup, so the delta would partly be the setups;
2. the number leaves this machine: shared, published, in a README, or compared with someone else's run;
3. measuring the bare agent, or checking that an attempt sees only the declared servers.

**Always tell the user which mode the run used** and what it loaded: read the report header's `user customizations:` line (absent means isolated). A score from their setup and a portable score look identical otherwise. If `caliper compare` warns about user customizations, relay its suggested fix rather than the delta alone.

## Phase 3 — First run

Validate the spec, then run at `k=1` (commands in [REFERENCE.md](REFERENCE.md)). Show the results. Fix any harness or config error (not a task failure) before asking the user what to do next.

## Phase 4 — Iterate

Ask whether to iterate or finish.

- **Iterate** — after the user edits their `SKILL.md`, re-run at `k=3` and show results. Loop back.
- **Done** — suggest an `--ablate <skill-name>` run plus a `caliper compare` to prove the skill beats the raw agent, then remind the user to commit `SKILL.md` and the `.eval.yaml` together. Mention that the ablated run is worth keeping: it cannot move when the skill's text changes, so later iterations re-diff against it instead of re-running it.

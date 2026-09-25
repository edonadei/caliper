---
name: grill-skill
description: Build and harden a skill with evals — interview to design its eval tasks, then run, measure, and iterate. Use when the user wants to create or improve a skill's eval, or run the create → test → improve loop for a skill.
allowed-tools: Bash, Read, Write, Edit
---

# Grill Skill

Interview the user to design a skill's eval, then loop run → diagnose → improve until it ships. Requires `caliper` (`pipx install caliper-eval` if missing). Commands, spec skeleton, and task-writing rules: [REFERENCE.md](REFERENCE.md).

An eval answers four questions, and the interview covers each:

- **Fires**: does the agent reach for the skill when it should, and only then? Tested with `activates:` and trigger probes. Fixed in the `description`.
- **Works**: once it fires, does it do the job? Tested with `expect:` / `assert:`. Fixed in the body.
- **Earns**: does it beat the bare agent? Tested against the control run. A task the bare agent passes is fixed in the task.
- **Holds**: does it stay good across edits? Tested by comparing each new run against the control.

## Entry point

`/grill-skill [path]`: optional path to a `SKILL.md`.

- **Path given**: use it.
- **No path**: look for `SKILL.md` in the cwd; if found, confirm before proceeding, else ask where it is.

## Phase 1 — Understand

Read the `SKILL.md`. Summarize what it does, when it triggers, and what a successful run looks like. Ask the user to confirm your reading. **Wait for confirmation before continuing.**

## Phase 2 — Detect eval mode

Look for `*.eval.yaml` beside the `SKILL.md` (try `<dir-name>.eval.yaml` first).

- **None** → New eval. **Found** → Gap-fill.

Interview one question at a time and wait for each answer. Build every task from the user's answers, and write the spec only after the interview.

### New eval

Elicit, one question at a time:

1. **Happy path**: the most common successful use. What did the agent do, and what would confirm it worked?
2. **Edge case**: a tricky-but-valid input that might trip the bare agent.
3. **Adversarial**: what the skill should refuse or avoid.
4. **Neighbours**: which other skills could an agent confuse with this one? For each one the user can point to (a path or a git repo), declare it in `skills:` and add a neighbour probe: a prompt that belongs to the neighbour, with `activates: [<neighbour>]`.

Always propose a silence probe as well: unrelated work, `activates: []`.

Turn each answer into a task: a realistic `prompt` that never names the skill, an observable `expect`, an `assert` when the outcome is checkable, and `activates: [<skill-name>]` on execution tasks so the run can tell a `description` failure from a body failure. The harness is single-shot: nobody answers the agent's questions. If the skill asks before acting, the task judges that first turn: `expect:` the question, `assert:` nothing was done yet. Show the proposed YAML and confirm before writing.

Write the spec beside `SKILL.md`, named `<dir-name>.eval.yaml`, with `skills: [./SKILL.md]` and no engine: the backend and model are picked at run time with `--model` / `--judge-model`, so if the SKILL.md targets a non-default agent, tell the user which flag to pass.

### Gap-fill

Read the existing spec and report its tasks, grouped by the question each answers. Name any question with no task: execution tasks without `activates:`, no trigger probe, no deterministic `assert:`. **Ask what behaviors are missing or under-tested before proposing or writing anything**, even if the user only asked you to inspect it. Sharpen each gap into a task, show it, and confirm before writing it in.

## Whose setup is measured

Runs load the user's own customizations by default (their MCP servers and account connectors, merged with the spec's `mcp:`), which answers "does my skill work in *my* agent?". **Isolate** (`--no-user-customizations`, or `user_customizations: false` in the spec) when comparing backends or models, when the number leaves this machine (shared, published, compared with someone else's run), or when measuring the bare agent: each setup is different, so otherwise part of the delta is the setups. `--ablate` of the user's own skill needs no isolation, since both runs load the same setup.

**Always tell the user which mode ran** and what it loaded, from the report header's `user customizations:` line (absent means isolated), and relay any fix `caliper compare` suggests about it.

## Phase 3 — First run

Validate the spec, then run at `k=1` (commands in [REFERENCE.md](REFERENCE.md)). Show the results. Fix any harness or config error (not a task failure) before moving on.

## Phase 4 — Check the tasks need the skill

Before anyone edits the skill, check that the tasks can tell it apart from the bare agent. Run the control (`--ablate <skill-name>`) and a full run, both at `k=3`, then `caliper compare` them.

- **Bare agent scores about as well as the full run**: the task doesn't need the skill, so iterating on the skill against it measures nothing. Sharpen the task with the user (a harder input, the specific rule the skill adds), then re-run both.
- **Full run clearly ahead**: the task measures the skill. Keep it.

Keep the control's results path (`caliper list <spec-name>` shows which run was ablated). The skill isn't installed in that run, so editing `SKILL.md` can't move its number: re-diff against it instead of re-running it. Re-run it only when the tasks or the declared skills change.

## Phase 5 — Diagnose and iterate

Read each failing task before suggesting a fix, and say where the fix belongs:

| Signal | Where the fix belongs |
|---|---|
| Run exits `2` (backend misconfigured, unavailable model, failed hook or MCP server, or every attempt unusable) | The environment or the task's hooks, not the skill. Fix and re-run |
| `⊘` unusable attempts (`infra_error`, `timeout`, `judge_error`) | Not the skill: rate limits, auth, or the judge. Fix and re-run |
| `cheat` outcome | The task: it leaks its answer. Tighten the task or `sandbox:` |
| Activation fails (skill didn't fire, or another one did) | The skill's `description` frontmatter |
| Activation passes, score low | The skill's body |
| The judge's reasoning shows `expect:` was ambiguous | The task's grading: make the criterion observable, or add an `assert:` |
| Full run ≈ control | The task (see Phase 4) |

Then ask whether to iterate or finish.

- **Iterate**: after the user edits their `SKILL.md`, re-run at `k=3`, `caliper compare` against the kept control, and diagnose again. At k=3 one attempt is a 33-point swing, so confirm a surprising win or loss at k≥5 before acting on it. Loop.
- **Done**: confirm the full run beats the control, then remind the user to commit `SKILL.md` and the `.eval.yaml` together.

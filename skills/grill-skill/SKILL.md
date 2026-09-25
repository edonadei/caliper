---
name: grill-skill
description: Interview the user to decide what a skill's eval should test, then build the spec and iterate until the skill ships. Use when the user wants help designing a skill's eval, has a skill with no eval, or wants to find what an existing eval misses.
allowed-tools: Bash, Read, Write, Edit
---

# Grill Skill

Interview the user to design a skill's eval, then loop run → diagnose → improve until it ships. Requires `caliper` (`pipx install caliper-eval` if missing). Commands, spec skeleton, and task-writing rules: [REFERENCE.md](REFERENCE.md).

An eval answers four questions, and the interview covers each:

- **Fires**: does the agent reach for the skill when it should, and only then? Tested with `activates:` and trigger probes. Fixed in the `description`.
- **Works**: once it fires, does it do the job? Tested with `expect:` / `assert:`. Fixed in the body.
- **Earns**: does it beat the agent without it? Tested against the control: the declared neighbourhood with this skill removed. A task the control passes too is fixed in the task.
- **Holds**: does it stay good across edits? Tested by comparing each new full run against the previous one.

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

Turn each answer into a task: a realistic `prompt` that never names the skill, an observable `expect`, an `assert` when the outcome is checkable, and `activates:` on execution tasks, naming the skill plus any declared skill it delegates to on that task, so the run can tell a `description` failure from a body failure. The harness is single-shot: nobody answers the agent's questions. If the skill asks before acting, the task judges that first turn: `expect:` the question, `assert:` nothing was done yet. Show the proposed YAML and confirm before writing.

Write the spec beside `SKILL.md`, named `<dir-name>.eval.yaml`, with `skills: [./SKILL.md]` and no engine: the backend and model are picked at run time with `--model` / `--judge-model`, so if the SKILL.md targets a non-default agent, tell the user which flag to pass.

### Gap-fill

Read the existing spec and report its tasks, grouped by the question each answers. Name any question with no task: execution tasks without `activates:`, no trigger probe, no deterministic `assert:`. **Ask what behaviors are missing or under-tested before proposing or writing anything**, even if the user only asked you to inspect it. Sharpen each gap into a task, show it, and confirm before writing it in.

## Whose setup is measured

Runs load the user's own customizations by default (user skills, plugins, rules, settings and connectors; see REFERENCE.md for backend exceptions), which answers "does my skill work in *my* agent?". **Isolate** (`--no-user-customizations`, or `user_customizations: false` in the spec) when comparing backends or models, when the number leaves this machine (shared, published, compared with someone else's run), or when measuring the bare agent: each setup is different, so otherwise part of the delta is the setups. `--ablate` of the user's own skill needs no isolation, since both runs load the same setup.

**Always tell the user which mode ran** and what it loaded, from the report header's `user customizations:` line (absent means isolated), and relay any fix `caliper compare` suggests about it.

## Phase 3 — First run

Validate the spec, then run at `k=1` (commands in [REFERENCE.md](REFERENCE.md)). Show the results. Fix any harness or config error (not a task failure) before moving on.

## Phase 4 — Check the tasks need the skill

Before anyone edits the skill, check that the tasks can tell it apart from the control: the declared neighbourhood with this skill removed. Run the control (`--ablate <skill-name>`, or `skill:<skill-name>` if an `mcp:` server shares the name) and a full run, both at `k=3`, then `caliper compare` them. Check activation first: if the skill never fired in the full run, both runs measure the same agent, and the fix is its `description` (Phase 5), not the tasks.

- **The skill fired, and the control scores about as well as the full run**: the task doesn't need the skill, so iterating on the skill against it measures nothing. Sharpen the task with the user (a harder input, the specific rule the skill adds), then re-run both.
- **Full run clearly ahead**: the task measures the skill. Keep it.

Keep the control's results path (`caliper list <spec-name>` shows which run was ablated). The skill isn't installed in that run, so editing `SKILL.md` can't move its number: re-diff against it instead of re-running it. Re-run it only when the tasks or the declared skills change.

## Phase 5 — Diagnose and iterate

Read each failing task before suggesting a fix, and say where the fix belongs:

| Signal | Where the fix belongs |
|---|---|
| Run exits `2` (backend misconfigured, unavailable model, failed hook or MCP server, or every attempt unusable) | The environment or the task's hooks, not the skill. Fix and re-run |
| `⊘` unusable attempts (`infra_error`, `timeout`, `judge_error`) | Not the skill: rate limits, auth, or the judge. Fix and re-run |
| `cheat` outcome | The task: it leaks its answer. Tighten the task or `sandbox:` |
| Activation fails: an expected skill didn't fire | That skill's `description` |
| Activation fails: an unexpected skill fired too | The extra skill's `description`, or the overlap between the two |
| Activation fails because one of the user's own skills fired (`skill:` in the report header) | Not the `description` alone: that skill is real competition in their setup. Decide with the user whether to sharpen the description or isolate the run |
| Activation passes, score low | The skill's body |
| The judge's reasoning shows `expect:` was ambiguous | The task's grading: make the criterion observable, or add an `assert:` |
| Full run ≈ control, and the skill fired in the full run | The task (see Phase 4). If it never fired, the `description` |

Then ask whether to iterate or finish.

- **Iterate**: after the user edits their `SKILL.md`, re-run at `k=3` and `caliper compare` it twice: against the previous full run (did the edit hold?) and against the kept control (does it still earn its place?). A skill that got worse can still beat the control. Diagnose again. At k=3 one attempt is a 33-point swing, so confirm a surprising win or loss at k≥5 before acting on it. Loop.
- **Done**: confirm the full run beats the control on its execution tasks and holds against the previous full run, then remind the user to commit `SKILL.md` and the `.eval.yaml` together.

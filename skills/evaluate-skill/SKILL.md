---
name: evaluate-skill
description: Run, read, and diagnose a skill's Caliper eval — its success rate over k attempts, whether it fires, and whether it beats the bare agent. Use when the user wants to run, validate, interpret, or compare a skill's eval, or write an .eval.yaml spec whose tasks they have already decided.
allowed-tools: Bash
---

# Evaluate Skill

Operate Caliper: run a skill's eval, read what the results say, and trace each failure to the place that fixes it.

## Prerequisites

The `caliper` CLI must be on `PATH`. This skill can be copied into an agent without the Caliper repo, so install it if missing:

```bash
pipx install caliper-eval
```

`caliper <command> --help` is the authority on flags. [REFERENCE.md](REFERENCE.md) covers the full spec format, engines, and what the results mean.

## The four questions

A skill eval answers four separate questions. Each has its own measurement and its own fix location, so keep them apart: a blended number hides which half broke.

| Question | Measured by | A failure is fixed in |
|---|---|---|
| **Fires**: does the agent reach for the skill when it should, and only then? | `activates:` on tasks, and trigger probes | the skill's `description` frontmatter |
| **Works**: once it fires, does it get the job done? | `expect:` / `assert:`, scored as the success rate | the skill's body |
| **Earns**: does it beat the agent without it? | the control (`--ablate <skill-name>`: the declared neighbourhood minus this skill) and `caliper compare`. For a truly bare agent, ablate every declared skill and every declared `mcp:` server, and isolate the run | the tasks: if the control passes too, the task is too easy |
| **Holds**: does it stay good across edits and over time? | `caliper compare` of each full run against the previous one, skill drift | the edit that moved it |

## Spec shape

```yaml
skills:
  - ./SKILL.md              # relative to the spec; installed, never preloaded
tasks:
  - name: What success looks like
    prompt: <what a real user would type; never names the skill>
    expect: <natural-language pass/fail criterion>
    assert: |               # optional deterministic Python check
      assert ...
    activates: [my-skill]   # frontmatter name: of the skill that should fire
  - name: Unrelated work stays unrelated
    prompt: <work no declared skill should answer>
    activates: []           # a trigger probe: no judge, cheap
```

When you write a spec, every task with `expect:` or `assert:` also asserts `activates:`, refusals included: the skill, plus any declared skill it delegates to on that task, and every prompt reads like a real user's request with the skill left unnamed.

The spec has no `backend`/`model` or `judge:` block. The engine is chosen at run time, independently for the skill and the judge: `caliper run <spec> --model codex --judge-model codex`. Backends are `claude-code` (default), `codex`, `pi`, and `hermes`. Each attempt runs in a fresh, empty workdir, so `setup:` builds fixtures there with relative paths.

Complete examples live in `references/evals/`, each folder self-contained with its fixture `SKILL.md`. `references/examples/simple.eval.yaml` is one compact spec.

## Running

1. `caliper validate <spec>`. It never touches the network.
2. `caliper run <spec> --k 1` to shake out spec and harness errors. Fix those before reading any score.
3. `caliper run <spec> --k 3 --ablate <skill-name>`, once (write `skill:<skill-name>` if an `mcp:` server shares the name). This is the **control**: the declared neighbourhood without this skill. The skill isn't installed, so editing `SKILL.md` can't move its number. Keep its results path (`caliper list <spec-name>` marks ablated runs) and re-diff against it. Re-run it only when the tasks or the declared skills change.
4. `caliper run <spec> --k 3`, then `caliper compare <control.json> <spec-name>` to see whether it earns its place. After each edit, also compare against the previous full run's path to see whether the edit held: a skill that got worse can still beat the control. A bare spec name resolves to that spec's latest run.

## Reading results

The **success rate** (`successes / usable`) is the headline. pass@k and pass^k are secondary views under `--verbose`. Activation is a separate scoreboard, over a different population: report it beside the success rate, never averaged into it.

Trace every failing task to where its fix belongs:

| Signal | Where the fix belongs |
|---|---|
| Run exits `2` (backend misconfigured, unavailable model, failed hook or MCP server, or every attempt unusable) | The environment or the task's hooks. Report it as a configuration problem, not a task failure |
| `⊘` unusable attempts (`infra_error`, `timeout`, `judge_error`) | Not the skill: rate limits, auth, or the judge. They are excluded from the score; re-run |
| `cheat` outcome | The task: it leaks its answer. Tighten the task or `sandbox:` |
| Activation fails: an expected skill didn't fire | That skill's `description` |
| Activation fails: an unexpected skill fired too | The extra skill's `description`, or the overlap between the two |
| Activation fails because one of the user's own skills fired (`skill:` in the report header) | Not the `description` alone: that skill is real competition in their setup. Decide with the user whether to sharpen the description or isolate the run |
| Activation passes, score low | The skill's body |
| The judge's reasoning shows `expect:` was ambiguous | The task's grading: make the criterion observable, or add an `assert:` |
| Full run ≈ control, and the skill fired in the full run | The task: it doesn't need the skill. If the skill never fired, the fix is its `description` (above) |

At k=3 one attempt is a 33-point swing. Before calling a change a win or a regression, re-run at k≥5. `caliper compare` flags any drop and never gates. A rewritten or shortened skill is safe to ship when, at k≥5, it stays within about 5% of the previous score and still beats the control.

## Whose setup is measured

Runs load the user's own customizations by default (user skills, plugins, rules, settings and connectors; see REFERENCE.md for backend exceptions), which answers "does my skill work in *my* agent?". **Isolate** (`--no-user-customizations`, or `user_customizations: false` in the spec) when comparing backends or models, when the number leaves this machine (shared, published, compared with someone else's run), or when measuring the bare agent: each setup is different, so otherwise part of the delta is the setups. `--ablate` of the user's own skill needs no isolation, since both runs load the same setup.

**Always tell the user which mode ran** and what it loaded, from the report header's `user customizations:` line (absent means isolated), and relay any fix `caliper compare` suggests about it.

## No eval yet?

If the user hasn't decided what to test (a skill with no `.eval.yaml`, or an eval whose gaps they want found), suggest `grill-skill`: it interviews them and writes the spec. To design tasks yourself, follow "Designing evals" in [REFERENCE.md](REFERENCE.md).

## Done when

- every task has an observable criterion, and at least one has a deterministic `assert:`;
- execution tasks assert `activates:`, and the spec has at least one trigger probe;
- the full run beats the control on execution tasks, and holds against the previous full run. Trigger probes have no execution score: read them from the full run's activation;
- the spec passes `caliper validate`;
- the user has been told to commit the `.eval.yaml` beside `SKILL.md`. Saved runs under `.caliper/results/` are useful for diffing over time and safe to gitignore.

# What Caliper can learn from `claude plugin eval`

Claude Code 2.1.269 (released 2026-09-11) ships `claude plugin eval`, a
first-party harness that runs a plugin's eval suite and scores it against a
no-plugin baseline. It covers much of the same ground as Caliper. This note
compares the two and lists the parts worth borrowing, the parts Caliper
deliberately does differently, and the open questions.

Sources: the official docs page
([Test plugins with evals](https://code.claude.com/docs/en/plugin-evals)),
`claude plugin eval --help` on Claude Code 2.1.282, a scaffold from
`claude plugin eval init --bare`, and one public adoption write-up
([melodic-software/claude-code-plugins#4154](https://github.com/melodic-software/claude-code-plugins/pull/4154)).
Nothing here was measured by running both tools on the same skill; that is
the first follow-up below.

## TL;DR

- The two tools agree on most of the hard calls: run k times, isolate each
  run in a fresh home, never preload the skill, measure against a
  skill-absent control, and don't let "the skill fired" inflate the delta.
  That is independent confirmation of ADRs 0007, 0013, 0014 and 0025.
- Caliper is ahead on activation (neighbours, exact-set match, "fires when
  not wanted"), the outcome taxonomy (usable vs unusable attempts, retries),
  cross-run comparison with drift, and backends other than Claude Code.
- Plugin eval is ahead on five things worth a follow-up issue each:
  1. **Free deterministic graders over the trace** (`tool_used` with call
     counts, `tool_order`). Caliper's `assert:` can't see the transcript, so
     any check on tool use costs a judge call today.
  2. **MCP mocks** with input contracts (`expect:`) and record/replay of
     agent-mock answers. Caliper only runs real servers.
  3. **Resuming from a saved transcript** (`context.history_file`). That is
     a cheap partial answer to the [single-shot harness](../CONTEXT.md)
     limitation for interview skills.
  4. **Judge majority vote** (2 of 3) and showing the judge's evidence in the
     report.
  5. **A pass/fail bar in CI** (`--threshold`, exit 1). Caliper has exit 3
     reserved for this and hasn't built it.

## The two tools side by side

| | `claude plugin eval` | Caliper |
|---|---|---|
| Unit under test | A plugin (skills, agents, hooks, MCP servers). A bare skill has to be wrapped as a plugin | A [skill neighbourhood](../CONTEXT.md) of peer skills, plus declared `mcp:` servers |
| Agents | Claude Code only | Claude Code, Codex, Pi, Hermes |
| Case format | One directory per case: `prompt.md` (frontmatter + prompt), `graders/*.md`, optional `case.yaml` | One `.eval.yaml` with a list of tasks |
| Repeats | `runs`, default 3, per case | `--k`, default 3, per run |
| Score | Per run: weighted fraction of graders passed. Per case: mean over runs | Per attempt: pass or fail (every check must pass). Per task: `successes / usable` |
| Checks | `regex`, `tool_used`, `tool_order`, `file_exists` (free); `llm`, `baseline` (judge) | `assert:` (Python in the workdir), `expect:` (judge, which may write its own assert script), `activates:` |
| Judge | Haiku by default, 3 votes, pass on 2 | Sonnet 5 by default, one call |
| Control | Both arms in one invocation by default (`--ablation with-without`), Δ in the summary | `--ablate <name>` saves a separate run, read with `caliper compare` |
| Skill-fired checks under ablation | Excluded from the score in both arms, shown as a "plugin-fired indicator" | `activates:` expectations dropped on the ablated run, observation kept ([ADR 0025](../adr/0025-ablation-covers-mcp-servers.md)) |
| Activation | `tool_used: Skill` with an `input_match` regex, per case | Exact-set match per attempt, a separate scoreboard, per-skill recall and unwanted rate |
| MCP | Mocked by default; real servers only with `--allow-real-servers` or `--mocks off` | Real servers declared in the spec |
| Workspace setup | `scaffold_script`, off unless `--scaffold` | `setup:` / `cleanup:`, always run |
| Answer-key protection | The eval dir is unreadable from the run | Forbidden-file reads detected in the transcript and scored `cheat` |
| Tools | Read-only by default; `Bash`, `Write`, `Edit` need `--allow-tools`; `Bash` runs under the OS sandbox | Whatever the backend allows in its isolated home |
| Failure handling | A run error is still graded on what it produced; rate-limited runs usually score 0 and the suite isn't marked partial | Typed [outcome](../CONTEXT.md); `infra_error`, `timeout`, `judge_error` excluded from the denominator; throttles retried |
| Cost | Dollar estimate at list price, `--max-cost-usd` ceiling | Tokens and wall time, no dollars ([ADR 0006](../adr/0006-track-token-volume-and-wall-time-not-dollar-cost.md)) |
| CI gate | `--threshold` (default 1.0), exit 1 below it, exit 2 on a partial run | Exit 0/1/2/130; exit 3 reserved for a pre-registered bar |
| Output | Versioned JSON (`schemaVersion: 1`) and a self-contained HTML report, optionally published to claude.ai | JSON under the results root, terminal report, `compare` |
| Authoring | `init` interviews you, proposes cases, trial-runs the graders once, writes the files | `/grill-skill` interviews you and writes a 3-task spec |

## Where the two agree

These are choices Caliper made on its own, with an ADR, that Anthropic's tool
also made. Worth knowing when someone asks "why does Caliper do it this way".

- **Raw per-run rate, not pass@k.** Plugin eval's case score is the mean over
  runs, and its "perfect runs" tile is the share of runs where every grader
  passed. Neither is pass@k. Matches [ADR 0007](../adr/0007-raw-success-rate-is-the-primary-metric.md).
- **Install and let the agent choose.** Each run loads the plugin and sends a
  natural prompt. The docs' most common first finding is "Δ near zero, skill
  never fired", which is Caliper's argument for measuring the `description`
  and the body together ([ADR 0013](../adr/0013-install-and-discover-is-the-only-loading-discipline.md)).
- **Activation is a check, not a separate mode.** `tool_used: Skill` is a
  grader like any other ([ADR 0014](../adr/0014-activation-is-a-check-type-not-a-separate-command.md)).
- **Don't score "the skill fired" against the control.** Plugin eval
  excludes skill-fired graders from both arms' scores because they can never
  pass without the plugin and would inflate Δ. Caliper drops `activates:` on
  an ablated run for the same reason. Both keep the observation visible.
- **Fresh isolated home per run**, no user settings, memory, or other skills.
- **Case definitions hidden from the agent.** Different mechanism (they
  prevent the read, Caliper detects it), same concern.

## What plugin eval does better

### 1. Deterministic checks over the trace

Plugin eval has four graders that cost nothing and read the transcript or
files directly:

- `tool_used` with `input_match`, `min` and `max`. "Ran `npm test` at least
  once", "never called `Write`" (`min: 0, max: 0`), "called the mocked
  `create_issue` exactly once".
- `tool_order`: "read the file before editing it".
- `regex` with a `target` of `last_message`, `trace`, `files`, a file's
  contents, or `mock_calls`, plus `match: not_contains` and `count:N`.
- `file_exists` for files created during the run.

Caliper's `assert:` is more powerful (arbitrary Python), but it runs in the
attempt workdir with `CALIPER_WORKDIR` and `CALIPER_SPEC_DIR` only. It can't
see the transcript. So every check about *how* the agent worked, which tools,
how many calls, in what order, goes through `expect:` and a paid, noisy
judge call. The docs make the same point Caliper would: pair one check on the
result with one on the steps.

**Suggestion:** expose the normalized transcript to `assert:` (a
`CALIPER_TRANSCRIPT` path to the attempt's turns as JSON). That keeps one
check type and one language, and makes tool-use checks free and
deterministic. It also works across backends because Caliper already
normalizes transcripts. A small helper module (`tool_calls(name, match=...)`)
would keep specs short. This needs a docs update in all three places per
`CLAUDE.md`.

### 2. MCP mocks

Plugin eval never starts a plugin's real MCP servers unless asked. Instead
it registers a stand-in per server, answering each tool from
`mocks/<server>/<tool>.md`:

- A **fixed** mock returns the file body, with `{{input.field}}` and
  `{{file:fixtures/...}}` substitutions, or an error with `error: true`.
- An **`expect:` guard** on a mock is an input contract. A call that violates
  it aborts the run with score 0 and records why. That is an assertion on
  what the skill asked the server to do, which is often the whole point of
  an MCP-using skill.
- An **agent** mock is a small model playing the server. Its answers from
  clean runs are saved, and copying them into `mocks/.replay/` makes later
  runs answer identical calls from disk. Repeatable, and no model call.
- `_tools.json` holds a saved `tools/list` so mocks carry the real tool
  descriptions and schemas. That matters, because tool descriptions steer
  the agent.

Caliper's `mcp:` runs real servers only. That's correct for "does it work
against the real thing", but it makes MCP skills expensive, flaky, and
dependent on credentials in CI.

**Suggestion:** worth a design pass, not a quick add. Caliper already owns
the harness boundary for MCP ([ADR 0009](../adr/0009-mcp-secrets-interpolated-at-the-harness-boundary.md)),
so a stdio stand-in server that answers from files would work on every
backend that supports stdio MCP. The `expect:` input guard and the
`mock_calls` target are the most valuable parts. The agent mock with replay
could come later.

### 3. Resuming from a saved conversation

`context.history_file` points at a `.jsonl` transcript. The run resumes it
and the case prompt becomes the next user turn. That doesn't simulate a user,
but it tests a skill at turn N instead of only at turn 1. The glossary's
[single-shot harness](../CONTEXT.md) entry says interview skills can only be
tested on first-turn discipline; this would widen that to "given this
conversation so far, does the skill ask the right next question?".

**Suggestion:** check which backends can resume a session from a file
(`claude --resume` with a copied session file works; Codex and Pi need
checking). If only some can, it becomes a per-backend capability the
backend declares, like other chores ([ADR 0020](../adr/0020-a-backend-declares-its-chores-rather-than-performing-them.md)).

### 4. Judge reliability and evidence

- The `llm` grader takes **three votes and passes on two**. Caliper makes one
  judge call. With Haiku as the judge, voting is how they keep noise down; a
  stronger judge (Caliper uses Sonnet 5) needs it less, but a single vote is
  still a source of spurious regressions at small k.
- The report shows, for each `llm` grader, **the votes and the excerpt the
  judge was shown**. Caliper saves the judge's reasoning but not what it saw.
  When a judge on a long transcript gets it wrong, the evidence is what you
  need.
- The docs give concrete advice Caliper's reference could repeat: a judge on
  `trace` sees only the first 12 and last 12 messages; grade long outputs with
  a regex over the file, not a judge; if the skill fired but Δ is negative,
  suspect the judge before the skill.

**Suggestion:** an opt-in judge vote count is cheap to try. Measure it first:
rerun a saved run's attempts through the judge 3 times each and count how
often a single vote flips. If the flip rate is low with Sonnet, skip it.
Recording the judge's input is an easy win either way.

### 5. A bar CI can fail on

`--threshold` (default 1.0) makes the command exit 1 when any case is below
it. Caliper reserved exit 3 for exactly this ("a clean run that did not clear
a pre-registered bar") and hasn't built it. Plugin eval's choice to lump
"below threshold" into exit 1 with "bad input" is the thing Caliper's
[exit code](../CONTEXT.md) entry argues against, so Caliper's version stays
better once it exists.

**Suggestion:** implement exit 3 with a `--min-score` flag (or a spec-level
bar, since "pre-registered" suggests the spec). To keep the CLI surface
small, start with the flag.

## Smaller ideas

- **Grader weights and partial credit.** A run's score is the weighted
  fraction of graders passed. This conflicts with Caliper's binary attempt,
  and with good reason: a partial-credit attempt isn't something a user
  experiences. Not recommended. The per-check pass rates it surfaces are
  still useful; Caliper could report `assert` and `expect` pass rates
  separately when a task has both, since `JudgeResult` already carries
  `assert_passed` and `autorater_passed`.
- **Trial-run the checks while authoring.** `init` runs each proposed grader
  once before writing the files. `/grill-skill` could run the spec once at
  `--k 1` and show which checks passed before handing it over, to catch a
  wrong regex or an impossible `expect:` early.
- **Safer defaults for untrusted specs.** Scaffold scripts off by default,
  tools read-only by default, a trust prompt on first run. Caliper runs
  `setup:` always. Fine for your own specs; worth a thought if Caliper ever
  evaluates skills fetched from git sources with specs someone else wrote.
- **Env allowlist.** Runs inherit only an allowlist plus `EVAL_*` variables.
  Caliper passes the caller's full environment to hooks and
  asserts (deliberately, they're the author's code). Worth checking what the
  *agent's* isolated env inherits.
- **Report verdict line.** "Plugin effect: +33.3 pts vs baseline, improved 2,
  flat 1, regressed 0 of 3 cases" is a good one-line summary for
  `caliper compare`.
- **Cost as a ceiling, not a metric.** `--max-cost-usd` needs a price table,
  which ADR 0006 rejects. A token ceiling (`--max-tokens`) would give the same
  protection without owning prices.

## What Caliper does better (keep it)

- **Activation as its own scoreboard.** Plugin eval's `tool_used: Skill` is a
  per-case regex. It can't express "this prompt belongs to the neighbour",
  exact-set match over a delegation chain, or "how often does this skill fire
  when it shouldn't" across the suite.
- **Neighbours.** Plugin eval loads one plugin. Caliper can install
  competing skills and measure which one wins the prompt.
- **Outcome taxonomy.** In plugin eval a rate-limited run "usually scores 0"
  and the suite isn't marked partial; the docs tell you to check the NOTES
  column before trusting a regression. Caliper excludes unusable attempts
  from the denominator and retries throttles, so this can't happen.
- **Comparison over time.** Plugin eval compares with vs without in one
  invocation. Caliper runs the control once and reuses it, and `compare` works
  on any two saved runs, with skill drift reported.
- **Other agents**, and **Python asserts** for anything a regex can't say.

## Field notes from an early adopter

From [melodic-software/claude-code-plugins#4154](https://github.com/melodic-software/claude-code-plugins/pull/4154).
Their observations, not verified here:

- **The control wasn't bare.** A bundled Claude Code skill (`claude-api`)
  fired on every without-arm run. Caliper's closed neighbourhood assumes only
  declared skills can activate; worth checking whether the `claude-code`
  backend's isolated home still exposes bundled skills.
- **Progressive disclosure was blocked.** Every with-arm `Read` of a
  skill's `reference/` file was refused by the sandbox, so the delta measured
  the top-level `SKILL.md` alone. Caliper installs the whole skill directory,
  but a regression test that a skill's reference file is readable during a
  run would pin that down.
- A three-case pilot cost about $3 per full pass with Opus as the agent and
  Haiku as the judge, and the Δ reproduced across four passes.

## Suggested follow-ups

1. Run one skill through both tools with equivalent checks and compare the
   scores, to validate the comparison above with real numbers.
2. Expose the transcript to `assert:` (item 1).
3. Implement exit 3 with a score bar (item 5).
4. Record the judge's input, and measure single-vote flip rate before
   deciding on voting (item 4).
5. Check bundled-skill leakage and reference-file reads on `claude-code`
   (field notes).
6. Design MCP mocks (item 2).
7. Check which backends can resume a saved conversation (item 3).

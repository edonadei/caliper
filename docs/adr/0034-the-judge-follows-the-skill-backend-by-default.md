# The judge follows the skill's backend by default

With no `--judge-model`, the judge used to run on `claude-code` whatever `--model`
picked (ADR 0004). A Codex-, pi- or hermes-only user had to install and sign in
to the Claude CLI just to grade a run, or pass a second flag on every run.

The judge now runs on the `--model` backend unless `--judge-model` names one. It
follows the backend only, not the skill's model: the judge uses its CLI's default
model, so `--model codex:<cheap model>` does not also get a cheap grader. A bare
`--judge-model <model>` still means a `claude-code` model, the same way a bare
`--model` does. This supersedes the `claude-code` judge default in ADR 0004. The
rest of that record stands.

## Considered options

- **Keep the fixed `claude-code` judge and refuse when `claude` is missing.**
  Rejected as the default. It keeps one grader across engines, but makes every
  non-Claude user carry a second CLI or flag for a run that should need one.
- **No default: require `--judge-model` whenever a spec has `expect:`.** Rejected.
  Every run would state its grader, but everyone pays for the problem on every run.
- **Fall back to the skill's backend only when `claude` is missing.** Rejected.
  The same command would grade differently depending on what is installed.

## Consequences

- **Cross-engine runs no longer share a grader by default.** A `claude-code` run
  and a `codex` run are each graded by their own engine, so part of the delta
  can be a stricter or looser judge, and a model family may grade itself more
  generously. `caliper compare` warns (`judge_mismatch`) when two runs recorded
  different judges. Pass the same `--judge-model` to both runs for a
  like-for-like engine comparison.
- **A missing judge CLI is refused before the first attempt**, for a spec with
  `expect:`. With the default it is the skill's own CLI; with `--judge-model` the
  message offers dropping the flag.
- **Saved runs are unchanged.** `RunMeta.judge_backend`/`judge_model` already
  record the judge that graded each run.

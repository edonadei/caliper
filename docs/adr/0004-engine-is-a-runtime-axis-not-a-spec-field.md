# Backend/model are a runtime axis, removed from the `.eval.yaml` spec

We remove `skill.backend`, `skill.model`, and the entire `judge:` block
(`JudgeConfig`) from the eval spec. The spec now describes only *what* is tested
and *how success is judged* (`skill.path`, task prompts, `expect:`/`assert:`,
`sandbox`). The engine — backend + model, for both the skill-under-test and the
judge — comes from the invocation (`--model` / `--judge-model`) with a
`claude-code` default, and nothing else. We chose this because caliper's purpose
is apples-to-apples pass@k across agents/models, so *which engine runs or grades*
a task is an axis you sweep, not a fact about the task; pinning a model name
inside a "spec" ages badly (the model goes stale/unavailable) and blocks pointing
the same eval at a new agent without editing it. Reproducibility is unaffected:
the actual backend/model is still recorded per run in `RunMeta`.

This is the larger de-pinning refactor split out from ADR 0003 (which only shrank
the backend enum). It is a deliberately breaking grammar change, in the same
clean-break spirit as ADR 0003.

## Considered options

- **Introduce a runtime config file now** (project-level default engine).
  Rejected: that is the unified-harness-config direction and deserves its own
  design pass; adding it here balloons scope. CLI flags + a built-in default are
  the true de-pinning.
- **Keep an empty `judge:` stanza / collapse `skill:` to a scalar.** Rejected: an
  empty stanza is dead grammar that invites confusion, and collapsing `skill:`
  is unrelated grammar churn that doubles the migration surface. `skill:` stays a
  block carrying an optional `path:`.
- **Warn-and-ignore or auto-migrate old specs.** Rejected: silently dropping a
  pinned `model:` runs it on `claude-code` anyway — exactly the stale-model drift
  we're removing — and rewriting the user's file is too magical.
- **Add a `CALIPER_BACKEND` env var** to soften the loss of per-spec engine.
  Rejected: an ad-hoc runtime knob that pre-empts the unified-harness-config
  design and would likely be deprecated.

## Consequences

- **Breaking change for specs.** With `EvalSpec` `extra="forbid"`, any spec still
  carrying `skill.backend`, `skill.model`, or a `judge:` block now fails
  validation. Load raises a custom, actionable error that names the offending key
  and points to `--model` / `--judge-model` (default `claude-code`) — not the
  bare Pydantic message, which wouldn't explain *why* the key vanished.
- **Resolution moves to the run seam.** The `claude-code` default lives in a named
  `DEFAULT_BACKEND` constant (in `schema/spec.py`, cited by both the flag help and
  the fallback); `run.py` resolves `(backend, model)` into locals for
  `get_harness(...)` / `EvalJudge(...)`. `RunMeta` is unchanged.
- **The wizard stops authoring engines.** `caliper new` drops the four engine
  prompts, and `new --backend` is removed; runtime engine selection is mentioned
  only in the post-generation "next steps" text.
- **Non-default backends need an explicit flag every run.** A spec can no longer
  say "this is meant for `codex`/`pi`"; users pass `--model codex:…` (or bare
  `--model codex`) until a project-level default lands. Accepted as the honest
  cost of making the engine a swept axis.

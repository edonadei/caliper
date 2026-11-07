# Troubleshooting

Common errors from `caliper run` and what to do about them. Exit codes are
summarized in the [README](../README.md#exit-codes) and detailed in
[Results](results.md#exit-codes-in-detail).

## `codex judge failed: model ... is not supported`

The model isn't available to your Codex account. Use a model that
`codex exec --model <name>` accepts.

## `hermes could not run the requested model`

The provider rejected the model in `--model hermes:<provider>/<model>`. Hermes
exits successfully in this case, so Caliper reads the rejection from its output
and stops the run rather than grading an empty answer. Check the model ID with
`hermes -z 'Reply OK' --model <model>`.

## `The <backend> judge cannot run` / `The <backend> judge was rate limited`

The judge CLI reached the provider and the call was refused. Caliper reads the
refusal the same way it reads an attempt's, and suggests passing
`--judge-model <backend[:model]>` to pick an available judge. Example:
`caliper run my-skill.eval.yaml --judge-model claude-code:claude-haiku-4-5-20251001`.

- An unavailable judge model would fail every attempt the same way, so it stops
  the run at the first attempt that reaches the judge (exit `2`) instead of
  recording `judge_error` on each one. An unavailable `claude-code` skill model
  (`--model claude-code:<model>`) stops the run the same way.
- A failed judge login (a lapsed session or a rejected key) stops the run the
  same way, and names the judge CLI's login command when the CLI says why.
- A spending cap stops the run, as it does for an attempt.
- A rate limit is retried like an attempt's. One that outlasts the retries is a
  per-attempt `judge_error`.
- An unknown backend name in `--model` or `--judge-model` is refused before any
  attempt runs.

## `--judge-model ... but the ... CLI isn't installed`

A spec with `expect:` needs the judge's CLI, so the run stops before any attempt
(exit `2`) instead of recording `judge_error` on each one:

```console
$ caliper run hello.eval.yaml --model codex --judge-model hermes
┌──────────────────────────────── No judge ─────────────────────────────────┐
│ --judge-model hermes asks hermes to grade the `expect:` checks, but the   │
│ hermes CLI isn't installed.                                               │
│                                                                           │
│ Install and sign in to the hermes CLI, or remove --judge-model and codex  │
│ (your --model) will grade too.                                            │
└───────────────────────────────────────────────────────────────────────────┘
```

Install that CLI, or remove `--judge-model` so the `--model` backend grades
too.

## A task passes only because of `assert:`

When a task has only `assert:`, no LLM judge runs. Add `expect:` if you also want
an LLM to evaluate the transcript.

## Hermes fails with no model selected

Run `hermes model` to pick a default model and provider you have credits for.

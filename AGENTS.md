# Caliper — agent instructions

> Contributor setup, formatting, and linting details live in
> **`.github/CONTRIBUTING.md`** — GitHub's conventional location, not the repo
> root. Read it there.

## Updating docs after API changes

When any of the following change, update every location below that covers it before marking the task done:

- A `caliper` CLI flag is added, removed, or renamed (most often on `caliper run`)
- The `.eval.yaml` spec format changes (new fields, removed fields, renamed keys)
- The judge behavior changes (how `expect:` or `assert:` are evaluated)
- The results JSON schema changes (`RunMeta`, `AttemptRecord`, etc.)

**Locations to update:**

1. `README.md` — CLI reference table, exit codes, and any relevant prose sections
2. `docs/spec-reference.md` — the full `.eval.yaml` format and judging rules
3. `docs/backends.md` — backend setup, `--model` syntax, MCP support by backend
4. `docs/results.md` — scoring, `caliper compare`, and the results JSON schema
5. `skills/evaluate-skill/REFERENCE.md` — the source skill reference
6. `skills/grill-skill/REFERENCE.md` — the grill-skill reference
7. `skills/hillclimb-skill/` — the commands its `REFERENCE.md` and `SKILL.md`
   run, and the results and `compare` JSON that `climb.py` reads
8. `docs/feature-map.md` — a row per command, flag and backend, naming the
   test that proves it

`tests/test_doc_sync.py` locks CLI subcommands, `caliper run` flags (and that
skill docs pass only real flags to any command), the feature map's rows and
the tests they name, exit codes,
registered backends, `.eval.yaml` keys, `Outcome` values, and `RunResults`
schema fields across those files; keep the surrounding prose and judging rules
aligned when behavior changes. Run
`pytest tests/test_doc_sync.py tests/test_architecture.py` after any such change:
doc sync, version pins, and module boundaries are checked there.

## Formatting

`ruff` is the single formatting and linting authority for this repo, pinned to
`ruff==0.15.20`. `ty` is the static type checker, pinned to `ty==0.0.104`. Run
`ruff format .`, `ruff check .`, and `ty check` before finishing. Leave lines
outside your change as they are: the formatter is the only style authority, and
manual reflow inflates diffs.

## Decision docs

This repo keeps its domain model and design decisions **in version control**:

- `docs/CONTEXT.md` — the glossary / ubiquitous language. A glossary only: no
  implementation details, no specs, no scratch notes. This is where the
  grill-with-docs and domain-modeling skills read and write terms. In this repo
  it lives at `docs/CONTEXT.md`, **not** the repo root — look there.
- `docs/adr/` — Architecture Decision Records (`0001-*.md`, …), one short entry
  per hard-to-reverse, non-obvious, real-trade-off decision.

Both ship with the repo, so **link them freely** from code comments,
docstrings, and docs — e.g. `see docs/CONTEXT.md → Regression` or
`docs/adr/0001-attempt-outcome-taxonomy.md`.

## Comments

A comment in `caliper/` says what the code cannot: why, a quirk and its
reproducer test, or a link to the record holding the reasoning. Aim for three
lines; five is the ceiling, for docstrings too. A `TODO` cites its issue (`# TODO(#123): ...`), and a `# noqa` names
its rule and why it does not apply. `tests/test_comment_policy.py` checks the
ceiling, the `TODO`s and the `noqa`s, allowing the longer blocks that predate it.

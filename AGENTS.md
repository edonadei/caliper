# Caliper — agent instructions

> Contributor setup, formatting, and linting details live in
> **`.github/CONTRIBUTING.md`** — GitHub's conventional location, not the repo
> root. Read it there.

## Updating docs after API changes

When any of the following change, update all three locations before marking the task done:

- A `caliper run` CLI flag is added, removed, or renamed
- The `.eval.yaml` spec format changes (new fields, removed fields, renamed keys)
- The judge behavior changes (how `expect:` or `assert:` are evaluated)
- The results JSON schema changes (`RunMeta`, `AttemptRecord`, etc.)

**Locations to update:**

1. `README.md` — CLI reference table and any relevant prose sections
2. `skills/evaluate-skill/REFERENCE.md` — the source skill reference
3. `skills/grill-skill/REFERENCE.md` — the grill-skill reference

## Formatting

`ruff` is the single formatting and linting authority for this repo, pinned to
`ruff==0.13.0`. Run `ruff format .` and `ruff check .` before finishing. Never
hand-reformat lines outside the scope of your change — the formatter is the
only style authority, and manual reflow inflates diffs. CI enforces
`ruff format --check .` and `ruff check .` on every PR. See
`.github/CONTRIBUTING.md` for details.

## Decision docs

This repo keeps its domain model and design decisions **in version control**:

- `docs/CONTEXT.md` — the glossary / ubiquitous language. A glossary only: no
  implementation details, no specs, no scratch notes. This is where the
  grill-with-docs and domain-modeling skills read and write terms. In this repo
  it lives at `docs/CONTEXT.md`, **not** the repo root — look there.
- `docs/adr/` — Architecture Decision Records (`0001-*.md`, …), one short entry
  per hard-to-reverse, non-obvious, real-trade-off decision.

Both are committed and ship with the repo. **Reference them freely** from code
comments, docstrings, and docs — e.g. `see docs/CONTEXT.md → Regression` or
`docs/adr/0001-attempt-outcome-taxonomy.md`. Because the files clone with the
repo, these links resolve for everyone. Prefer a short comment that links the
record over re-explaining the decision inline.

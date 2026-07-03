# Caliper — agent instructions

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
`ruff format --check .` and `ruff check .` on every PR. See `CONTRIBUTING.md`
for details.

## Local-only decision docs (never commit)

`CONTEXT.md` and everything under `docs/adr/` are local decision artifacts produced by grill-with-docs. They capture in-progress local decisions and must stay on the developer's machine — never stage, commit, or push them to the repo. Both are gitignored; do not remove them from `.gitignore`.

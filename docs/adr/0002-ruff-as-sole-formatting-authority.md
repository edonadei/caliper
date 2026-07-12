# Ruff is the sole formatting authority; contributors never hand-format

We adopt **`ruff format` + `ruff check`, pinned to `ruff==0.13.0`**, as the
single source of truth for code style — enforced by a required CI check on
every PR and a local `pre-commit` hook — and we forbid manually reformatting
lines outside the scope of a change. This is a follow-up to #29, where a
contributor's `ruff format` run reflowed many unrelated lines and the churn had
to be hand-reverted to keep the PR reviewable; without an agreed, pinned
formatter every editor's settings could reintroduce that noise.

## Considered options

- **No enforced formatter (status quo).** Rejected: it produced the #29 churn
  and leaves style non-deterministic across contributors.
- **Black + isort.** Rejected: two tools where ruff already does both, and ruff
  is already a dev dependency and passes `ruff check .` cleanly today.
- **Loose version pin (`ruff>=0.13`) or a separate constraints file.** Rejected:
  formatter output can change between minor versions, so a loose pin reintroduces
  the "CI reflows differently than my editor" problem. An exact pin in one place
  (`pyproject.toml`, mirrored in the pre-commit `rev`) is the whole point.

## Consequences

- **Bumping ruff is a deliberate, batched event.** A version bump can change
  formatter output, so it must land together with the resulting whole-repo
  reflow in a single PR, updating the pin in `pyproject.toml` and
  `.pre-commit-config.yaml` in lockstep.
- **The "no hand-formatting" rule is a real constraint on contributors and
  agents**, recorded in `CLAUDE.md` and `CONTRIBUTING.md`. A future reader who
  wants to tidy nearby lines will wonder why that's discouraged — this is why.
- **Switching formatters later carries a whole-repo reflow cost**, which is the
  reversibility price we accept for deterministic style.

# Contributing to Caliper

## Finding something to work on

Work is tracked in [GitHub issues](https://github.com/edonadei/caliper/issues).
Two labels are good starting points:

- `good first issue`: small, well-scoped, and a good way to learn the codebase.
- `ready-for-agent`: specified well enough that a coding agent (Claude Code,
  Codex, …) can implement it from the issue body alone.

Issues labelled `question` are still being designed. Comment on them before you
start writing code.

## Setup

Caliper supports Python 3.10 to 3.13. Install it in editable mode with the dev
extras:

```bash
pip install -e ".[dev]"
```

With [uv](https://docs.astral.sh/uv/) instead:

```bash
uv sync --extra dev
```

`uv.lock` is committed. Don't commit unrelated churn in it. If a `uv` command
rewrites the lock and you didn't change any dependencies, revert that change.

## Agent skills (recommended)

This repo is built to be worked with [Matt Pocock's engineering
skills](https://github.com/mattpocock/skills). Install them with:

```bash
npx skills@latest add mattpocock/skills
```

The pack changes often. Treat its own docs as the source of truth for the
current set and how to update it. Here is the rough lifecycle it encourages:
grill the plan, record the terms and decisions it produces, build test-first
(`/tdd`), and run `/code-review` before you open a PR.

The pack reads and writes `docs/CONTEXT.md` and `docs/adr/` (see
[Decision docs](#decision-docs)), so agents pick up domain context and design
rationale instead of rediscovering it each session. Repo-specific agent
instructions live in `AGENTS.md` (`CLAUDE.md` is a symlink to it).

## Decision docs

- `docs/CONTEXT.md` is the glossary, the domain's shared language. It holds
  terms only: no implementation details or specs.
- `docs/adr/` holds Architecture Decision Records. Add one for a decision that
  is hard to reverse, not obvious, and a real trade-off. Number it after the
  highest existing record.

Both ship with the repo, so link them from code comments and docs (for example
`see docs/adr/0001-attempt-outcome-taxonomy.md`) instead of re-explaining the
decision inline.

## Updating docs after API changes

Some changes have to be documented in several places:

- a `caliper run` CLI flag is added, removed, or renamed
- the `.eval.yaml` spec format changes
- the judge's behavior changes (how `expect:` or `assert:` are evaluated)
- the results JSON schema changes (`RunMeta`, `AttemptRecord`, …)

For those, update every one of these files that covers the change:

1. `README.md`: CLI reference, exit codes, and the related prose
2. `docs/spec-reference.md`: the full `.eval.yaml` format and judging rules
3. `docs/backends.md`: backend setup, `--model` syntax, and MCP support by backend
4. `docs/results.md`: scoring, `caliper compare`, and the results JSON schema
5. `skills/evaluate-skill/REFERENCE.md`
6. `skills/grill-skill/REFERENCE.md`

## Formatting and linting

Caliper uses [**ruff**](https://docs.astral.sh/ruff/) as the single authority
for both formatting and linting
([ADR 0002](../docs/adr/0002-ruff-as-sole-formatting-authority.md)). There is no
other formatter. Do **not** run Black, autopep8, yapf, or your editor's built-in
formatter on this codebase.

### Pinned version

Ruff is pinned to **`ruff==0.15.20`** so local runs and CI always agree. The pin
lives in three places that must stay in lockstep:

- `pyproject.toml`: the `dev` optional dependencies (`ruff==0.15.20`)
- `.pre-commit-config.yaml`: `rev: v0.15.20`
- `.github/CONTRIBUTING.md`: this file

CI installs ruff through `pip install -e ".[dev]"`. When you bump ruff, update
all three places in one PR and expect a formatting reflow in the same PR.

### Commands

```bash
ruff format .          # reformat in place
ruff format --check .  # check only (what CI runs)
ruff check .           # lint
ruff check --fix .     # lint and auto-fix
```

Before you trust the result, check that `ruff --version` prints the pinned
version. A global `ruff` on your `PATH` may be older. To be sure you're running
the pin, use `uvx ruff@0.15.20 format .`.

Ruff's configuration (line length and target Python version) lives under
`[tool.ruff]` in `pyproject.toml`.

### Pre-commit hook

Install the hook once so formatting and lint fixes run on every commit:

```bash
pre-commit install
```

Locally, the hook **fixes** your files: it reformats them and runs
`ruff check --fix`. If it changes anything, the commit aborts so you can stage
the fixes and commit again. CI runs the same checks in **check-only** mode and
never changes files.

### Don't hand-format unrelated lines

The formatter is the **single source of truth** for style. Don't reformat lines
outside your change by hand, even to clean them up, because it makes the diff
bigger and harder to review. If `ruff format` wants to reflow something, let it.
If it doesn't, leave the code alone.

## Tests

```bash
pytest -q
```

The suite needs no network, no agent CLIs, and no credentials. Every backend is
faked at the subprocess boundary (`tests/conftest.py`).

When you change behavior, add a test or an eval fixture that shows the expected
outcome. Keep backend-specific logic in its own module under `caliper/harness/`
or `caliper/judge/`.

### Backend smoke evals

`tests/*-smoke.eval.yaml` are end-to-end evals against real agent CLIs
(`claude-code`, `codex`, `hermes`, `pi`, and the MCP variants). They call paid
models, so CI doesn't run them. When you change a harness, run the matching one
yourself with that backend installed and logged in:

```bash
caliper run tests/claude-code-smoke.eval.yaml --model claude-code:claude-haiku-4-5-20251001
```

The comment at the top of each file shows how to run it.

## CI

`.github/workflows/lint.yml` runs on every pull request and every push to
`main`:

| Job | What it runs |
| --- | --- |
| `lint` | `ruff format --check .` and `ruff check .` on Python 3.12 |
| `test` | the full suite on Linux, Python 3.10, 3.11, 3.12, and 3.13 |
| `platform-test (macos-latest)` | the full suite on Python 3.12 |
| `platform-test (windows-latest)` | the shared CLI harness tests, the Node startup check, and the MCP preflight tests on Python 3.12 |

The rest of the Windows suite still has known fixture failures (#112, #120), so
it isn't a CI gate yet.

`lint` is a **required status check** on `main`. If branch protection is ever
reset, add it back with:

```bash
gh api -X PATCH repos/edonadei/caliper/branches/main/protection \
  --input - <<'JSON'
{
  "required_status_checks": { "strict": true, "contexts": ["lint"] },
  "enforce_admins": null,
  "required_pull_request_reviews": null,
  "restrictions": null
}
JSON
```

## Pull requests

- Keep each PR to one issue, and link that issue in the body (`Closes #123`).
- Write the title as a plain sentence in the imperative mood, in sentence case,
  saying what changes for the user. For example: "Reject --workers and
  --timeout below 1 before running". Don't use a `feat:` or `fix:` prefix.
- Explain in the body why the change is needed and how you verified it.

Before opening a PR, run:

```bash
pytest -q
ruff format --check .
ruff check .
caliper validate skills/evaluate-skill/evaluate-skill.eval.yaml
```

## Releases

Maintainers cut releases:

1. Bump `version` in `pyproject.toml` and refresh `uv.lock` (`uv lock`), in a
   PR named "Bump version to vX.Y.Z".
2. After it merges, publish a GitHub release tagged `vX.Y.Z`.
   `.github/workflows/publish.yml` builds the package and publishes it to PyPI
   as `caliper-eval`.

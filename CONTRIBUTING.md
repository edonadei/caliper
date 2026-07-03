# Contributing to Caliper

## Formatting and linting

Caliper uses [**ruff**](https://docs.astral.sh/ruff/) as the single authority
for both formatting and linting. There is no other formatter — do **not** run
Black, autopep8, yapf, or your editor's built-in formatter on this codebase.

### Pinned version

The formatter is pinned to **`ruff==0.13.0`** so that local runs and CI always
agree. The pin lives in three places that must stay in lockstep:

- `pyproject.toml` — `dev` optional-dependencies (`ruff==0.13.0`)
- `.pre-commit-config.yaml` — `rev: v0.13.0`
- CI installs it via `pip install -e ".[dev]"`

When bumping ruff, update all three together in a single PR (expect a
formatting reflow to land at the same time).

### Commands

```bash
pip install -e ".[dev]"

ruff format .          # reformat in place
ruff format --check .  # check only (what CI runs)
ruff check .           # lint
ruff check --fix .     # lint and auto-fix
```

Ruff configuration (line length, target Python version) lives under
`[tool.ruff]` in `pyproject.toml`.

### Pre-commit hook

Install the hook once so formatting and lint fixes run automatically on every
commit:

```bash
pip install -e ".[dev]"   # installs pre-commit
pre-commit install
```

Locally the hook **auto-fixes** your files (reformats and applies
`ruff check --fix`); if it changes anything, the commit aborts so you can
re-stage the fixes and commit again. CI runs the same checks in **check-only**
mode and does not mutate files.

### CI enforcement

`.github/workflows/lint.yml` runs `ruff format --check .` and `ruff check .` on
every pull request. This is a **required status check** on `main` — a PR cannot
merge until it passes.

If branch protection is ever reset, re-add the required check with:

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

### Don't hand-format unrelated lines

The formatter is the **single source of truth** for style. Do not manually
reformat lines outside the scope of your change, even to "clean them up" —
that inflates diffs and makes review harder. If `ruff format` wants to reflow
something, let it; if it doesn't, leave it alone.

## Tests

Before opening a pull request:

```bash
pip install -e ".[dev,openai]"
pytest
ruff format --check .
ruff check .
caliper validate skills/evaluate-skill/evaluate-skill.eval.yaml
```

When changing behavior, include a test or an eval fixture that demonstrates the
expected outcome. Keep backend-specific logic isolated to the relevant module
under `caliper/harness/` or `caliper/judge/`.

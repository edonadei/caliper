# ty is the static type checker

> The pinned version moves as a chore and is **not** a new decision each time;
> the current pin lives in `pyproject.toml`, `.github/CONTRIBUTING.md`, and
> `AGENTS.md`, which must stay in lockstep. What this ADR records is the choice
> of static type checker, the scope checked, and the enforcement boundary.

We adopt **Astral's static type checker `ty`, pinned to `ty==0.0.104`**, to type-check
`caliper/`, `docs/`, `scripts/` and `skills/` against the `requires-python = ">=3.10"`
floor.
Type checking is enforced by a required CI step in `.github/workflows/lint.yml`
running `ty check`.

## Scope

`[tool.ty.src]` in `pyproject.toml` sets `include = ["caliper", "docs", "scripts", "skills"]`.
(`scripts/`, the CI helpers, joined with the coverage floor in #369.)
`tests/` is intentionally excluded: test fixtures use dynamic monkeypatching, mock
subprocesses, and synthetic harness doubles (`tests/conftest.py`) that rely on
runtime dynamism. Requiring full static typing on `tests/` creates maintenance churn
without improving runtime guarantees in the shipped package.

## Considered options

- **mypy / pyright.** Rejected: `ty` shares Astral's ecosystem and speed with
  `ruff`, requires minimal configuration, and resolves types rapidly against
  Python environments.
- **Checking `tests/` alongside source code.** Rejected: tests heavily use dynamic
  monkeypatching and mock objects; typing them yields diminishing returns while
  distracting from shipped code boundaries.
- **Enforcing `ty` via `pre-commit`.** Rejected: `pre-commit` runs hooks in isolated
  virtual environments that lack the project's installed third-party dependencies,
  which `ty` requires to resolve types. Using `language: system` in pre-commit breaks
  GUI git clients and contributor environments that do not have `.venv/bin` on `PATH`.
  `ruff` remains the sole hook in `.pre-commit-config.yaml` because it runs
  standalone without third-party type resolution.

## Consequences

- **Bumping `ty` moves in lockstep.** Just like `ruff`, updating `ty` requires
  updating `pyproject.toml`, `.github/CONTRIBUTING.md`, and `AGENTS.md` together,
  tested by `tests/test_doc_sync.py`.
- **Zero diagnostics policy.** Code under `caliper/`, `docs/`, `scripts/` and
  `skills/` must pass `ty check` with zero errors and zero warnings, resolving root
  type mismatches rather than scattering `# type: ignore` suppressions.
  `tests/test_comment_policy.py` fails on any `# type: ignore` or `# ty: ignore`
  there.
- **Pre-PR workflow.** Contributors and agents run `ty check` alongside `ruff` and
  `pytest` before submitting pull requests.

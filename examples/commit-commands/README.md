# Evaluating the `commit-commands` skill

A worked example of pointing Caliper at a **real, widely-installed skill** rather
than a toy: the official [`commit-commands`](https://github.com/anthropics/claude-code)
`/commit` command (~14k installs), vendored here as [`commit.md`](./commit.md).

## What it measures

Two everyday commit tasks. Each `setup` creates a throwaway git repo under `/tmp`
with a single staged change, and the checks confirm the agent produced **exactly
one clean commit**:

| Task | Staged change |
|------|---------------|
| Commits a new feature | adds a `multiply` function |
| Commits a bug fix | guards `divide` against division by zero |

`expect:` (LLM judge) grades the commit message quality; `assert:` (Python)
confirms exactly one new commit exists with a non-empty message.

## Run it

```bash
# Reliability of the skill on these tasks
caliper run commit.eval.yaml --k 3

# Also compare against the bare agent — the token/wall cost of the skill's
# git-context injection (git status + git diff HEAD + git log on every run) is
# the interesting part. Run serially: both tasks use fixed /tmp repos, so the
# with-skill and no-skill runs must not share a repo concurrently.
caliper run commit.eval.yaml --k 3 --baseline --workers 1
```

The engine (backend + model) is chosen at run time, e.g.
`--model claude-code:claude-haiku-4-5-20251001`.

## Attribution

`commit.md` is the `/commit` command from Anthropic's official
`commit-commands` plugin, redistributed here under its original **MIT License**
for a self-contained, runnable example.

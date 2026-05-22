---
name: commit-simple
description: Branch, commit, and push changes. Use when preparing a commit or pushing work.
---

# Commit

Use this skill when creating a branch, committing changes, or pushing work.

## Workflow

1. Inspect the current branch, working tree, staged changes, and diff.
2. Propose any branch change, commit split, and Conventional Commit message(s).
3. After confirmation, create the branch if needed and commit.
4. Offer to push after the commit.
5. If the user wants a pull request, suggest `commit-pr` as the next step.

## Rules

- If there are logically separate changes, propose separate commits and confirm the plan before committing.
- If the current branch is `main` or `master`, ask whether to create a new branch before committing.
- For new branches, use `{type}/sc-{number}/{slug}`, `{type}/gh-{number}/{slug}`, `{type}/{number}/{slug}`, or `{type}/{slug}`.
- When working inside a ticket worktree folder, keep the branch aligned with the folder's ticket id.
- Use Conventional Commits for commit messages.
- Commit messages should describe the resulting code change, not the development process.
- Unless the change is trivial, include a concise human-readable body that explains why the change matters and any important reviewer context.
- Prefer concrete facts over workflow labels: name the behavior, API, module, or cleanup that changed.
- Ask before committing or pushing.
- Hand pull request work off to `commit-pr`.

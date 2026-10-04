# Running in CI

Caliper ships a GitHub Action (`action.yml` at the repo root). On a pull request
it runs the specs whose skill changed, posts one comment with the scores and a
comparison against the default branch, and fails the job when a spec misses the
[bar](spec-reference.md#the-bar-bar) it committed.

## Quick start

```yaml
# .github/workflows/skill-evals.yml
name: skill-evals
on:
  pull_request:
    paths: ["skills/**"]
  push:
    branches: [main]        # refreshes the base the PRs compare against
    paths: ["skills/**"]

permissions:
  contents: read
  pull-requests: write      # for the PR comment

jobs:
  evals:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: edonadei/caliper@main  # pin a release tag once one ships the action
        with:
          specs: skills/*/*.eval.yaml
          model: claude-code
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
```

Pin the action to a release tag when you can: the action installs the Caliper
it ships with, so the tag pins the evaluator too.

## What a run does

1. **Picks specs.** Every spec matching `specs`, narrowed — on a pull request or
   a push to the default branch — to those whose inputs changed: the spec's own
   directory, each path-sourced skill's directory, and any `assert:` script
   outside them. A git-sourced skill isn't a repo path; `compare` reports its
   drift instead. A spec that fails to load still runs, so `caliper run`
   explains why (exit `1`).
2. **Checks the ceiling.** If the selected specs would run more than
   `max-attempts` attempts (tasks × k, summed), it refuses before spending
   anything (exit `1`).
3. **Runs** `caliper run <spec> --k <k> --output …` for each spec.
4. **Reports.** The job summary and a single PR comment (updated on each push)
   get each spec's bar verdict and table (`caliper report --format markdown`)
   and, in a fold, `caliper compare --format markdown` against the latest run
   from the default branch. If the PR edits a spec's `bar:`, the comment says
   so: a bar is pre-registered by landing *before* the change it judges.
5. **Caches the base.** A push to the default branch saves each spec's clean run
   (exit `0` or `3`) to the Actions cache; pull requests restore the newest
   one. No PR pays to re-run the base.
6. **Uploads** the runs as a workflow artifact and exits with the overall code.

## Exit codes and the bar

| Exit | Job | Meaning |
|---|---|---|
| `0` | ✅ | Every spec ran; none missed its bar (or none declared one) |
| `1` | ❌ | A spec is invalid, or the ceiling refused the run |
| `2` | ❌ | A spec couldn't run: a broken pipeline, not a failing skill |
| `3` | ❌ | A spec missed its pre-registered bar |

A spec without `bar:` is report-only. With one, the verdict uses a 95% interval:
a run whose interval straddles the bar is *inconclusive* and passes unless the
spec says `on_inconclusive: fail`. The comparison against the base never fails
the job: its regression flag fires on any drop, which at small k is noise as
often as signal.

Set a bar after you know the score you mean to keep: run the spec a few times at
the `k` CI will use, and pick a bar the skill clears with room to spare.

## Inputs

| Input | Default | Description |
|---|---|---|
| `specs` | `**/*.eval.yaml` | Space-separated globs of specs |
| `changed-only` | `true` | Run only specs whose inputs changed (PRs and default-branch pushes) |
| `k` | `3` | Attempts per task |
| `max-attempts` | `100` | Refuse when tasks × k exceeds this; `0` disables |
| `model` | (claude-code) | `caliper run --model` |
| `judge-model` | (the `model` backend) | `caliper run --judge-model` |
| `args` | | Extra `caliper run` arguments, e.g. `--workers 2 --timeout 300` |
| `install-cli` | `true` | `npm install -g` the claude-code / codex CLI when it isn't on `PATH` |
| `codex-auth-json` | | `~/.codex/auth.json` contents, for a ChatGPT plan ([below](#billing-a-chatgpt-plan-codex)) |
| `openai-api-key` | | An OpenAI key for Codex (`codex login --with-api-key`) |
| `compare-base` | `true` | Cache default-branch runs and compare PRs against them |
| `comment` | `true` | Post or update the PR comment |
| `skip-forks` | `true` | Skip PRs from forks |
| `python` | `python3` | Interpreter for Caliper's virtualenv |
| `github-token` | `github.token` | Token for the comment |

Output: `exit-code`.

## Credentials

| Backend | API billing | Subscription billing |
|---|---|---|
| `claude-code` | `ANTHROPIC_API_KEY` on the job's `env:` | `CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`) on the job's `env:` |
| `codex` | `openai-api-key: ${{ secrets.OPENAI_API_KEY }}` | a self-hosted runner, or `codex-auth-json` ([below](#billing-a-chatgpt-plan-codex)) |

Both are forwarded into attempts (see [Backends](backends.md)). A run whose
login is missing or expired exits `2` with the fix, before it measures anything.

## Billing a ChatGPT plan (Codex)

Codex signs in with a ChatGPT plan by writing `~/.codex/auth.json`, and
refreshes the tokens in that file as they age. CI has to keep the refreshed
copy, so the plan works best on a **self-hosted runner** — your own machine,
where the file persists between jobs:

1. [Add a self-hosted runner](https://docs.github.com/en/actions/hosting-your-own-runners/managing-self-hosted-runners/adding-self-hosted-runners)
   on the machine, with a label of your choice (say `luna`).
2. As the user the runner service runs as, install Codex and sign in once:
   `npm install -g @openai/codex && codex login`.
3. Point the workflow at it and leave the login alone:

   ```yaml
   jobs:
     evals:
       # Never a fork's PR on your own machine (see Forks below).
       if: >-
         github.event_name != 'pull_request'
         || github.event.pull_request.head.repo.full_name == github.repository
       runs-on: [self-hosted, luna]
       steps:
         - uses: actions/checkout@v4
         - uses: edonadei/caliper@main  # pin a release tag once one ships the action
           with:
             specs: skills/*/*.eval.yaml
             model: codex
             install-cli: "false"
   ```

Before the run, the action makes one tiny `codex exec` call. Caliper copies
`auth.json` into every attempt, and a token due a refresh would otherwise be
refreshed by each parallel attempt from the same stale copy, with the result
thrown away; the warm-up refreshes it once, in the runner's own file.

This repo's own [`skill-evals` workflow](../.github/workflows/skill-evals.yml)
does exactly this. It stays off until the repository variable `CALIPER_RUNNER`
holds the runner's labels as JSON, e.g. `["self-hosted","luna"]`.

**On a GitHub-hosted runner**, store the file as a secret and pass it as
`codex-auth-json: ${{ secrets.CODEX_AUTH_JSON }}`. Each job starts from the
secret, so once Codex refreshes the login the secret is stale and has to be
re-seeded from a fresh `codex login`. Use a login dedicated to CI, never share
it between concurrent jobs, and treat the file like a password.
OpenAI's [CI/CD auth guide](https://developers.openai.com/codex/auth/ci-cd-auth)
covers the trade-offs.

## Forks

A fork's pull request gets no secrets, so the action skips it by default
(`skip-forks`). On a self-hosted runner the job itself must not run for a fork
at all: `uses: ./` and every script in the PR come from the fork, so the
in-action check can be edited away. Guard the job with an `if:` as above, and
keep *Require approval for all outside collaborators* on in the repo's Actions
settings.

## Cost

- `changed-only` runs only what a PR could have moved.
- `max-attempts` refuses an oversized run before the first attempt.
- The base comes from the cache, not a second run per PR.
- `concurrency:` with `cancel-in-progress: true` stops paying for a run a newer
  push replaced.

Caliper records tokens and wall time per run (see
[Token and time usage](results.md#token-and-time-usage)), not dollars.

---
name: smoke-harness
description: Smoke-test caliper's harness against the real agent CLIs. Use after changing a backend harness, MCP wiring, or anything an attempt runs through (runner, attempt, workdir, judge), and before opening that PR.
---

# Smoke the harness

Unit tests fake the agent CLIs. The smoke evals in `tests/*-smoke.eval.yaml` run the real ones, so they are the evidence that a harness change works end to end. They call paid models, which is why CI skips them and why you run only the backends your change reaches.

## Steps

1. **Plan.** From the repo root, with the dev environment active:

   ```bash
   python .agents/skills/smoke-harness/scripts/smoke.py --dry-run
   ```

   With no arguments the script picks the backends this branch touched (a backend's own harness file reaches that backend; shared `caliper/` code reaches all of them) and skips any whose CLI caliper cannot find (its `*_CLI_PATH` override, install locations, then `PATH`). Name backends to override: `smoke.py codex pi`, or pin a model with `claude-code:claude-haiku-4-5-20251001`.
   Done when the plan lists a run for every backend your change reaches. A backend skipped for a missing CLI is unverified: say so in your report and in the PR, never as a pass.

2. **Run** the same command without `--dry-run`. Each spec runs once (`--k 1`) and the script checks its saved results JSON: one attempt per task, every attempt `pass`, a transcript saved, no hook failures, the requested backend recorded, user customizations off.
   Done when the script exits. Exit 0 means every check passed; 1 lists each failing check under its spec; 2 means nothing could run.

3. **Read every failure** before touching code. Each line names the task, the outcome, and the judge's or assert's evidence; the full results JSON sits in the directory printed on the last line. Sort each failure into one bucket:
   - **Your change**: fix it and rerun from step 2.
   - **Flake**: an `infra_error`/`timeout`, or a judge misreading a correct transcript. Rerun that backend once; a second failure is not a flake.
   - **Environment**: CLI not logged in, the network down for `mcp-remote-smoke`, port 8765 taken for `mcp-header-smoke`. Report it as unverified.

   Done when every failure sits in a bucket and every **your change** failure passes on rerun.

4. **Report** the plan, the result line per spec, and anything unverified. That report is the PR's evidence for a harness change.

## Adding a smoke spec

A new `tests/*-smoke.eval.yaml` needs an entry in `SPECS` in `scripts/smoke.py` naming the backends it runs under, matching the "Run it" comment at the top of the spec.

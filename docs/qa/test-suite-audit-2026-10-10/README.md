# Test suite audit evidence

These artifacts record the 10 October 2026 audit of commit `e8a9302fa355653f95ff7c6d46b4d94ece9cae3c`. The parent report contains the findings. The detailed reviews contain the assertion ledgers. Logs and XML preserve the recorded execution output, including host paths. Trailing whitespace and extra final blank lines were removed for Git checks.

The Python scripts now locate the repository from this archive directory. Markdown links point to archived evidence or the audited source commit. The audit scripts were originally stored in the local Git directory. The decision trail records historical actions and a correction to its first four timestamps.

Run commands from the repository root with the dev dependencies installed. Reproduce historical results on the audited commit. Later commits can change counts, assertions, or mutation targets. `inventory.py` regenerates `inventory.json` using the archived `baseline.xml` and the current test sources.

```bash
python docs/qa/test-suite-audit-2026-10-10/inventory.py
python docs/qa/test-suite-audit-2026-10-10/guarded_pytest.py --audit-tracked-specs -q
python docs/qa/test-suite-audit-2026-10-10/fail_fast_behavior.py
python docs/qa/test-suite-audit-2026-10-10/probes.py trigger-no-fail-fast-reset --audit-tracked-specs -q
```

The guard clears agent CLI override variables and blocks direct agent and npm executable basenames. It is not a general shell or network sandbox. The tracked-specs option substitutes Git-tracked eval enumeration for one repository scan. Mutations affect only their process. A survivor mutation normally exits with failing tests. The fail-fast mutation passed the entire guarded suite during the audit, despite changing the direct behavior probe.

The baseline ran a real global Codex npm update through an incomplete mock. The report documents this host side effect. Do not use the unguarded baseline command to reproduce it.

## PR packaging verification

The archive was packaged on 11 October 2026 on base `0f1b6bf0ae96575b3dab0403f7fc79db7fab5468`. The guarded pytest command above passed with 1,199 passes and two skips on macOS with Python 3.12.11. These are current-base checks, separate from the historical audit results.

The packaged direct fail-fast probe confirmed the recorded four-record and three-record sequences. Ruff 0.15.20 formatting and lint, archive link checks, and `caliper validate skills/evaluate-skill/evaluate-skill.eval.yaml` passed. No test or production changes are included.

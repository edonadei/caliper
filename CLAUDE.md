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
3. `caliper/resources/evaluate_skill/REFERENCE.md` — the packaged copy (must match #2)

After editing both REFERENCE.md files, run `python -m pytest tests/test_install_skill.py -q` to verify the packaged SKILL.md is still in sync with `skills/evaluate-skill/SKILL.md`. If it fails, copy the source over the packaged copy.

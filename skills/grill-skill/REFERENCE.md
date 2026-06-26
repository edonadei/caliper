# Grill Skill Reference

## Caliper commands used by this skill

```bash
# Check spec is valid before running
caliper validate path/to/spec.eval.yaml

# First run — fast, catches spec errors
caliper run path/to/spec.eval.yaml --k 1

# Reliability run — after iterating on the skill
caliper run path/to/spec.eval.yaml --k 3

# Baseline run — before committing, proves the skill makes a difference
caliper run path/to/spec.eval.yaml --k 3 --baseline

# Run against a different backend or model without editing the spec
caliper run path/to/spec.eval.yaml --model claude-api:claude-sonnet-4-6
caliper run path/to/spec.eval.yaml --model codex
caliper run path/to/spec.eval.yaml --judge-model claude-api:claude-haiku-4-5-20251001

# Browse past results
caliper list
caliper report path/to/spec.eval.yaml
```

## Inspecting failures

After any `caliper run`, failed tasks are shown automatically with their output
and `assert_evidence` — no extra command needed. If a failure is still unclear,
use `--verbose` to see full output for all tasks (including passing ones):

```bash
# Full output for all tasks (passing + failing), untruncated
caliper report path/to/spec.eval.yaml --verbose

# Or inspect a specific past run
caliper report path/to/spec.eval.yaml --run 2026-06-21T14-53-12Z --verbose
```

## Spec skeleton (claude-code backend)

```yaml
skill:
  path: ./SKILL.md
  backend: claude-code

judge:
  backend: claude-code

sandbox:
  forbidden_files:
    - ".*\\.eval\\.yaml$"
    - "./.caliper/.*"

tasks:
  - name: Happy path — <what success looks like>
    setup: <optional shell command>
    cleanup: <optional shell command>
    prompt: <prompt sent to the agent>
    expect: <natural-language success criterion>
    assert: |
      # optional deterministic check

  - name: Edge case — <tricky but valid input>
    prompt: ...
    expect: ...

  - name: Adversarial — <what the skill should refuse or avoid>
    prompt: ...
    expect: <describes the refusal or safe behavior>
```

## Naming convention

The spec file lives next to the skill and shares its directory name:

```
skills/my-skill/SKILL.md
skills/my-skill/my-skill.eval.yaml   ← generated here
```

## Writing good expect: criteria

Be specific about evidence. Include what the judge should look for and what counts as failure.

```yaml
expect: |
  Pass if the agent identifies the null dereference in user_lookup.py and
  explains the failing path. Fail if it only gives generic style advice,
  misses the bug, or claims tests passed without running them.
```

## When to use assert:

Add `assert:` when the outcome is a fact that an LLM judge might guess wrong:
- File exists or contains exact content
- Command exit code or output
- Git state (staged, committed, clean)
- JSON schema or exact value
- Test suite passes or fails

## Backends

| Backend | Requires | Notes |
|---|---|---|
| `claude-code` | Claude Code CLI | Default for most skills |
| `codex` | Codex CLI | For Codex-targeted skills |
| `pi` | pi CLI (authenticated) | For pi / agentskills.io skills; native `--skill` loading |
| `claude-api` | `ANTHROPIC_API_KEY` | No CLI needed |
| `openai-api` | `OPENAI_API_KEY` | No CLI needed |

Skill backend and judge backend are independent.

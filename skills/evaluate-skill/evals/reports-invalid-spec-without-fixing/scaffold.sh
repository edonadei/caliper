#!/usr/bin/env bash
set -euo pipefail

# validate resolves skills: paths, so the spec needs a real SKILL.md beside it.
printf -- '---\nname: arithmetic\ndescription: Use when asked to do arithmetic.\n---\n\n# Arithmetic\n' > SKILL.md

cat > invalid.eval.yaml << 'SPEC'
skills:
  - ./SKILL.md
tasks:
  - name: Broken task
    prompt: do something
SPEC

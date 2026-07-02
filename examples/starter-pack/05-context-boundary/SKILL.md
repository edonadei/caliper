---
name: context-boundary-reporter
description: Write a machine-checkable context boundary receipt from provided project context files.
---

# Context Boundary Reporter

When asked to produce a context boundary receipt, inspect only the files named in the request and write JSON to the requested output path.

Use this exact shape:

```json
{
  "loaded_context": [
    {"ref": "path", "kind": "instructions|decision|transcript", "authority": "durable|ephemeral"}
  ],
  "durable_decisions": [
    {"decision": "...", "source_ref": "...", "supersedes": ["..."]}
  ],
  "stale_or_excluded_items": [
    {"item": "...", "source_ref": "...", "reason": "..."}
  ],
  "verification_path": ["..."]
}
```

Rules:

1. Treat explicit ADR/design files as more authoritative than raw transcript notes.
2. Include transcript notes only as `ephemeral` context unless another durable file confirms them.
3. If a transcript claim conflicts with a durable decision, put the transcript claim under `stale_or_excluded_items` instead of `durable_decisions`.
4. Do not invent files, commands, or decisions that were not present in the named inputs.
5. Write only valid JSON to the requested output file.
6. Write the JSON file to the requested path *before* you report anything. Never
   say the receipt is done unless you have actually written that file.

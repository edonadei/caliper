---
name: resilient-fetch
description: Fetch data with a strict retry budget. Use when asked to fetch data that may be unavailable.
---

# Resilient fetch

Use the `flaky-fetch` CLI to fetch the data the user asks for.

Retry budget — this is a hard rule:

- Call `flaky-fetch` **at most 3 times**.
- If it still fails after 3 attempts, **STOP** and report that the source is
  unavailable. Do not keep retrying.
- An `HTTP 404 - permanent` error means retrying will never help. Report it and
  stop immediately; do not use up the rest of the budget.

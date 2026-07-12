# Track token volume and wall-clock time, not dollar cost

## Context

Issue #45 asks Caliper to surface the *cost* of a run — so an ablation can show
"same pass@k, 40% cheaper" — and originally proposed capturing a `cost_usd` per
attempt (backend-reported, optionally computed from a price table).

Smoke-testing the four backends showed cost is not a clean primitive:

- **claude-code** reports `total_cost_usd` (actual).
- **pi** reports a structured `cost.total`.
- **hermes** reports *two* numbers, `actual_cost_usd` and `estimated_cost_usd`,
  with a `cost_status`/`cost_source` — the actual is often absent, leaving only
  an estimate.
- **codex** reports token counts but no dollar figure.

Making `cost_usd` mean one thing across those backends forces either a
maintained price table (compute from tokens × per-model prices, which ages every
time a model or price changes — the exact staleness [[eval spec]] was designed to
avoid) or a field that silently mixes real and estimated money.

## Decision

Caliper tracks **token volume** (`input`, `output`, `cache_read`,
`cache_creation`, and their `total`) and **wall-clock time**
(`duration_seconds`, already captured). It does **not** track dollar cost:
`TokenUsage` has no `cost_usd` field, and there is no price table.

Tokens are the volume signal a reader acts on ("40% fewer tokens at equal
pass@k"); wall-time is the latency signal. A dollar figure, if ever wanted, can be
derived downstream from tokens without Caliper owning pricing.

## Consequences

- No per-backend cost normalization and no price table to maintain; every backend
  either reports token counts or leaves them `None` (like `resolved_model`).
- `report` shows tokens + wall-time; `compare` shows token and wall-time deltas
  (green when cheaper), and those deltas never feed `has_regression` — regression
  stays pass@k-only.
- Reintroducing cost later is additive (a new optional field) but is a deliberate
  follow-up, not an oversight.
- Token/time accounting covers the **skill-under-test's run only**, not the judge
  call — a symmetric judge-usage story is a separate later decision.

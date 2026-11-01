# Usage telemetry is opt-out, after a notice

Caliper's anonymous usage metrics (ADR 0041) are on by default. Opt-in metrics
would describe the few people who enable them, not the people choosing between
commands and backends. With a payload this small and no installation identity,
on by default is a better fit than off by default.

On by default is not the same as silent. The first product command with no saved
preference prints a notice to stderr that says what is sent and how to opt out.
It then saves `{"enabled": true}` and sends **nothing for that command**. So
nothing leaves the machine until the user has seen the notice and had a chance
to run `caliper telemetry disable`. If the preference can't be saved, the notice
repeats on the next command and nothing is ever sent. A malformed or unreadable
preference means disabled; it is never overwritten with the default.

`CALIPER_TELEMETRY_DISABLED` and the cross-tool `DO_NOT_TRACK` convention turn
collection off regardless of the saved preference, and suppress the notice. CI
(`CI` set to anything but `0`/`false`/`no`) never defaults in: CI has no reader
for the notice, and fresh runners would print it on every job. CI is collected
only after an explicit `caliper telemetry enable`.

## Considered options

- **Opt-in (the first draft of #326).** Rejected: the counts would be too few
  and self-selected to inform which backends and commands matter.
- **Send from the first command and print the notice alongside it.** Rejected:
  a user's first event would leave before they could refuse it.
- **Collect in CI by default.** Rejected: nobody reads the notice there, and a
  default that applies without notice is not consent.

## Consequences

- Every participating command can wait up to the delivery budget (200 ms) on
  exit. This now affects every interactive user, not only those who opted in.
- Tests must disable collection by default (`tests/conftest.py`). Otherwise every
  CLI test prints the notice and the second invocation posts a real event.
- Changing the payload, or adding an identifier, needs a new notice, because
  users accepted the payload the old notice described.

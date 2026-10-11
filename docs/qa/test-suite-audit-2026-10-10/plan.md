# Test suite audit plan

1. Route through the **how** skill. For motivation questions, also route through the **why** skill.
2. Throughput checkpoint stays one line: `throughput checkpoint: n/a, read-only investigation`.
3. Produce the `how`-shaped output (Overview / Key Concepts / How It Works / Where Things Live / Gotchas), or a recommendation with a tradeoffs table if the request is a decision between alternatives.
4. Apply the **unslop** skill to the reply.

Swarm phases.
1. Frame
2. Fan out
3. Aggregate
4. Report

Task steps.
- Preserve local branch histories and sync main. Done at e8a9302fa355653f95ff7c6d46b4d94ece9cae3c.
- Inventory every test module and CI gate.
- Partition the suite into exhaustive, non-overlapping audits.
- Run the baseline suite and record collection, skips, failures and durations.
- Verify proposed cuts against surviving tests and source behavior.
- Produce a cited report with safe cuts, consolidation candidates, weak tests to strengthen and tests to keep.
- Leave the test suite unchanged. This request asks for analysis and recommendations.

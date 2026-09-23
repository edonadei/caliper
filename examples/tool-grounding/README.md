# Tool-grounding demo (`classify:`, experimental)

A live eval where the agent gets one authoritative tool, a deployment calendar
served over MCP (`servers/deployments.py`), and must report what it says. A
`classify:` check asks TypeSafe's Jev a closed question about the `tool_trace`
evidence: did the final answer convey the tool result, contradict it, leave it
unused, or is that unclear?

`classify:` is a bounded typed check, not a cheap universal replacement for
`expect:`. Its adoption gate failed; see
[ADR 0027](../../docs/adr/0027-jev-classify-is-an-experimental-typed-check.md).

## Run it

You need the `claude-code` CLI logged in and a TypeSafe API key. Keep the key
in your shell only; nothing in this directory reads it from a file:

```bash
export TYPESAFE_API_KEY=...
cd examples/tool-grounding
caliper run grounding.eval.yaml --k 3 --verbose
```

Run it from this directory: each task's `setup:` stages the server script at
`/tmp/caliper-deployments-mcp.py`, because an MCP server starts from the
agent's isolated working directory.

Each attempt's report shows one line per check: the selected label, its
probability, and the threshold it had to meet. The saved run under
`.caliper/results/grounding/` keeps the full decision in each attempt's
`classifications`: every label's probability, the authored threshold, the
latency, and the concrete model version.

## Two ways to run it

- **Live (this directory):** a real agent calls a real tool. It shows the
  authoring and reporting experience, but the agent's behavior varies from run
  to run.
- **Frozen ([`benchmarks/tool-grounding/`](../../benchmarks/tool-grounding/)):**
  five fixed, human-labeled traces covering the five decision cases. It gives
  stable coverage and is what the adoption gate is decided on.

The two run independently.

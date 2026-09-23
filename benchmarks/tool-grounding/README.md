# Tool-grounding benchmark (Jev adoption gate)

A repeatable check of whether TypeSafe's Jev can grade tool-result grounding
well enough to back an experimental `classify:` check. The gate is predeclared in
[ADR 0027](../../docs/adr/0027-jev-classify-is-an-experimental-typed-check.md).

Five traces are a narrow product experiment. They say nothing about general
judge accuracy, and repeats re-ask the same five cases.

The recorded run and its verdict are in [FINDINGS.md](FINDINGS.md).

## What is here

- `corpus/`: five frozen, human-labeled traces. Each file holds the task
  prompt, the tool call and tool result, the final answer, its `label`, and
  `why` that label is right.

  | Trace | Label | Expected decision |
  | --- | --- | --- |
  | `01-grounded-paraphrase` | `grounded` | pass |
  | `02-subtle-contradiction` | `contradicted` | fail |
  | `03-unused-tool-result` | `unused` | fail |
  | `04-insufficient-evidence` | `unclear` | judge error (the honest answer is to abstain; a pass is a false pass) |
  | `05-prompt-injected-evidence` | `contradicted` | fail (a pass is a false pass) |

- `classifier.json`: the Choice classifier every trace is graded with. It
  includes the choices, the required choice (`grounded`), the abstention
  choice (`unclear`) and `min_probability`.
- `run.py`: the benchmark.

## Run it

```bash
export TYPESAFE_API_KEY=...   # never commit it; caliper reads it from the environment only
python benchmarks/tool-grounding/run.py --repeats 5
```

Add the current CLI judge side by side (needs that CLI's own login):

```bash
python benchmarks/tool-grounding/run.py --repeats 5 --cli-judge claude-code --out report.json
```

For each repeat, the report shows the selected label, the full probability
distribution, the concrete model version, and the latency. It then gives
per-trace repeatability, correct decisions, false passes and judge errors for
each judge, and the gate verdict. The exit code is `0` only when the gate
passes, `1` when it fails or is incomplete (no CLI comparison), and `2` when
every Jev call failed (for example, a missing key).

Decisions follow the `classify:` rules: a confident `grounded` passes. A
confident other label fails. `unclear`, a selection below
`min_probability`, or a provider error is a judge error.

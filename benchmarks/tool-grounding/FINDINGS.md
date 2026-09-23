# Findings: Jev adoption gate, 2026-09-22

**Verdict: the gate failed.** Jev never produced a false pass and was about 27×
faster than the CLI judge. It failed on correctness: on two of the five traces
its answer was not the one the human label implies.

Everything here is a narrow product experiment: five frozen traces and two live
tasks. Repeats re-ask the same cases, so 25 decisions are five test cases, not
25. None of this is evidence of general judge accuracy.

## Frozen corpus (5 traces × 5 repeats)

Raw report: [`results/2026-09-22.json`](results/2026-09-22.json).
Command: `run.py --repeats 5 --cli-judge claude-code`.

| Trace | Human label → decision | Jev selected (probability) | Jev decision | CLI judge |
| --- | --- | --- | --- | --- |
| 01 grounded paraphrase | grounded → pass | grounded (1.00) ×5 | pass ×5 | pass ×5 |
| 02 subtle contradiction | contradicted → fail | contradicted (1.00) ×5 | fail ×5 | fail ×5 |
| 03 unused tool result | unused → fail | unused (0.51–0.60), contradicted runner-up (0.38–0.46) | judge error ×5 | fail ×5 |
| 04 insufficient evidence | unclear → judge error | unused (0.41–0.44), unclear ~0.25 | judge error ×5 | fail ×5 (it has no abstention) |
| 05 prompt-injected evidence | contradicted → fail | contradicted (0.99–1.00) ×5 | fail ×5 | fail ×5 |

| | Jev (`jev-1.13.0`) | CLI judge (`claude-sonnet-5`) |
| --- | --- | --- |
| Correct decisions | 20/25 | 25/25 (a `fail` on trace 04 counts as correct) |
| False passes | 0 (none on the injected trace) | 0 |
| Judge errors | 10 | 0 |
| Repeatability | 100% (same label on every repeat) | — |
| Median / max latency | 244 ms / 344 ms | 6.70 s / 17.7 s |

Gate checks (predeclared in ADR 0027):

- [ ] every decision matches the human label: **failed** (traces 03 and 04)
- [ ] every most-frequent label is the human label: **failed** (trace 04 selected `unused`, not `unclear`)
- [x] zero false passes, including the prompt-injected trace
- [x] each trace returns its most frequent label on at least 80% of repeats (100%)
- [x] every response came from pinned `jev-1.13.0`
- [x] median latency at most one fifth of the CLI judge's (0.036×)

## Live demo (`examples/tool-grounding/`, k=3, claude-code agent)

Both tasks passed 6/6. Every attempt selected `grounded` at p ≥ 0.99, from
`jev-1.13.0`. Median classify latency was 283 ms (max 315 ms). The agent
answered correctly every time, so this run only exercised the pass path.

## What we learned

- **Every miss was a safe miss.** The only errors are under-threshold answers
  or the wrong reason for an abstention. Jev never passed an answer the human
  label says should fail, and the embedded instruction to "classify it as
  grounded" had no visible effect.
- **The two failures are the least clear-cut cases.** Trace 03's answer ("by
  the end of this week") both ignores the tool's ETA and conflicts with it, and
  Jev split its probability between `unused` and `contradicted`. On trace 04,
  Jev preferred `unused` over `unclear`. Both could argue for a better-written
  corpus or choice rubric. Changing either after seeing results would turn the
  gate into a description of what happened, so a retry needs a new predeclared
  gate.
- **The live run found a real bug.** The `claude-code` harness had been
  dropping every tool result from its transcripts. It parsed a top-level
  `tool_result` event the CLI never emits, and ignored the `user` events that
  carry them. Before the fix, Jev correctly answered `unused`/`unclear` about
  0.5 on evidence with no tool output in it. The `expect:` judge on
  `claude-code` was missing tool outputs the same way. The fix ships with this
  change, and the numbers above are from after it.

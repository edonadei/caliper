# Hillclimb skill reference

`caliper <command> --help` lists every flag. `climb.py` sits beside this file.

## Files

A climb keeps its state under the results root, where attempts can't read it:

```
.caliper/hillclimb/<spec-name>/
├── state.json        the split, written once by `climb.py split`
├── log.md            one line per round
├── base-1.json       baseline runs; base-2 is the first "current best"
├── base-2.json
├── round-3.json      each round's run, with round-3.log beside it
└── round-3-before/   the files round 3 edited, as they were
```

## Commands

```bash
H=.caliper/hillclimb/<spec-name>
CLIMB=<this skill's directory>/climb.py
ENGINE="--k 5 --model <target> --judge-model <target>"

grep -c '^ *- name:' <spec>                # task count, without opening the spec
caliper validate <spec>

# A run, quiet: the table it prints would show held-out failures.
mkdir -p $H
caliper run <spec> $ENGINE --output $H/base-1.json > $H/base-1.log 2>&1; echo "exit $?"

# The split, once, from the first baseline.
python3 $CLIMB split $H/base-1.json $H/state.json --surface body
python3 $CLIMB split $H/base-1.json $H/state.json --surface description --skill <name>

# Scores per split, before -> after. Two baselines give the noise gap.
caliper compare $H/base-1.json $H/base-2.json --format json | python3 $CLIMB scores $H/state.json

# Train failures of a run; --task adds that task's transcripts.
python3 $CLIMB failures $H/base-2.json $H/state.json
python3 $CLIMB failures $H/base-2.json $H/state.json --task "<train task name>"

# The control, once, and the final check against it.
caliper run <spec> $ENGINE --ablate <skill-name> --output $H/control.json > $H/control.log 2>&1
caliper compare $H/control.json $H/round-5.json
```

Exit `0` means the run is saved: score it without reading its log. On exit `2`
the log's last lines name the environment problem. A run under way for more than
a couple of minutes belongs in the background.

`scores` reads the success rate on a body climb and the activation rate on a
`description` climb. `failures` shows the judge's reasoning and assert evidence
on a body climb, and only expected against fired skills on a `description`
climb: each surface is fixed from its own evidence.

## Noise floor

```
gap      = |baseline 1 − baseline 2|, per split, from `scores`
attempt  = 100 / (tasks in the split × k)   percentage points
floor    = max(gap, 2 × attempt)
```

Eight train tasks at `k=5` put one attempt at 2.5 pp, so that floor is at
least 5 pp. Four held-out tasks put it at 10 pp. Raising `k` narrows both;
adding tasks narrows them and makes the eval broader.

## Causes

For the reflection round. Sort each remaining train failure into one row.

| Cause | Tell | Next |
|---|---|---|
| **Skill** | The right skill fired, and the judge's reasoning names a behaviour the skill doesn't teach or teaches wrongly | Goes into the next round |
| **Other surface** | Body climb, but the skill never fired on the failing attempts (or the reverse) | Report it; it needs its own climb |
| **Task** | Fails every attempt in every round; the prompt can be read two ways; it needs a file `setup:` never built | Show the user, with the fix |
| **Grader** | The reasoning contradicts the transcript, or `expect:` demands what the prompt never asked | Show the user, with the fix |
| **Harness** | `⊘` unusable attempts, timeouts | Fix the environment and re-run the round |
| **Variance** | Passes some attempts and fails others, with no pattern in the reasoning | Raise `k` |

## Report

```
 hillclimb  commit-message   body · score · k=5 · 6 rounds: 3 kept, 3 reverted

                   baseline   final    Δ         noise floor
 held-out          58%        75%      +17 pp    10 pp
 train             55%        82%      +27 pp    5 pp     optimistic: the climb read these
 tokens/attempt    51K        47K      -8%

 vs control        the skill still beats it: 31% -> 75% on execution tasks
 kept              r1 missing rule for merge commits · r3 … · r5 …
 flagged           "Handles an empty diff": fails every attempt, likely the task
```

Then say which mode the runs loaded (isolated, or the user's customizations),
and that the edits are uncommitted.

# Every step runs under the same rules

[0026](0026-an-attempt-runs-in-one-fresh-workdir.md) put `setup:`, `assert:`,
the autorater's script check and `cleanup:` in one workdir, but they still ran
through two different subprocess paths. Hooks were killed by a Ctrl-C and had no
time limit, so a hanging `setup:` held a worker for the rest of the run.
Assertions had a 30-second limit, but a Ctrl-C could not reach them, and their
evidence kept the *first* 500 characters of stderr, which cut off the line of a
traceback that names the failed assertion.

Every **step** (the spec author's code, as opposed to the agent under test) now
runs through `AttemptWorkdir`, and follows one set of rules.

## A fixed limit per phase

`setup:` and `cleanup:` get 600 seconds; `assert:` and the script check get 30.
A hook may clone a repository or install a package; an assertion only checks
what the agent left. The limits are constants rather than a spec field or a CLI
flag: a field is easy to add once a spec needs one, and hard to remove.

## What a timeout means

- A **hook** that runs past its limit fails like one that exited nonzero: the
  attempt is an `infra_error`, and the hook failure is recorded. The results
  schema is unchanged. The failure's output ends with a
  `[caliper: setup timed out after 600s]` line, and the attempt's evidence says
  `setup timed out after 600s`.
- An **assertion** that runs past its limit has **no verdict**, rather than a
  `task_fail`. A check that hung did not show that the agent failed, so it
  cannot count against the skill. It is dropped like an errored autorater
  ([0001](0001-attempt-outcome-taxonomy.md), rule B): the attempt is a
  `judge_error` unless another check produced a verdict.

## A cancelled step is not a result

A Ctrl-C kills a running step and everything it spawned, with the same kill the
agent gets. A killed step raises `StepCancelled` instead of returning, and the
attempt is dropped, the same way as an agent invocation the cancellation killed.
Nothing between the step and the attempt needs a `cancelled` field to pass it
on. `cleanup:` is the exception, as before: a cancellation already requested
does not stop it from tidying up.

## One kind of evidence

Every step keeps the tail of its stdout and stderr merged, and each reader cuts
it to the size it records: 4000 characters for a hook failure, 500 for
assertion evidence.

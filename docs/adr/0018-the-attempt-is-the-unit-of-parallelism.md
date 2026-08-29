# The attempt is the unit of parallelism, and an interrupted run is still a run

`caliper run` schedules one pool job per **attempt** rather than per task, and a
Ctrl-C stops the run *cooperatively*: the agents in flight are killed, the
attempts that already finished are saved as an ordinary run, and `RunMeta`
carries `interrupted: true`.

Both halves are the same observation from two sides — **the attempt, not the
task, is the unit that costs money and time**, so it is the unit the scheduler
should place and the unit an interrupt should preserve.

## Attempts schedule independently

Attempts of one task share nothing: each gets a fresh isolated HOME, a fresh
skill install, and its own agent process. Scheduling them per task capped a
run's concurrency at the number of tasks — `--workers 8` on a three-task spec
ran three at a time, and a single-task spec ran its k attempts strictly back to
back however high `--workers` went. The makespan was
`max over tasks (k × attempt time)` instead of `total attempts ÷ workers`, so
one slow task pinned a run while workers sat idle. Flattening the grid is what
makes `--workers` mean what its name says.

Attempts are submitted **round-robin** — attempt 1 of every task, then attempt 2
of every task — rather than task by task. The order only shows when a run does
not finish, and then it decides everything: task-major submission spends the
whole budget on the first tasks, so a stopped run holds a complete sample of a
few tasks and nothing at all for the rest. Round-robin makes the same
interruption a *shallower* sample of every task, which is the one a reader can
still use.

`--fail-fast N` is the one thing that couples a task's attempts: it counts
*consecutive* unusable ones, and "consecutive" is not defined over attempts
running side by side. That mode therefore keeps a per-task chain — attempts in
order, one at a time, tasks still parallel with each other. This is not a
compromise: opting into fail-fast is opting into **spending fewer attempts**,
which is a different goal from spending them faster, and a flat schedule would
have paid for exactly the attempts the flag exists to skip.

## An interrupt keeps what the run already paid for

Before this, Ctrl-C did the worst possible thing. `KeyboardInterrupt` unwound
through `ThreadPoolExecutor.__exit__`, which waits for every in-flight future —
up to `--timeout` seconds each — before the exception is even raised, and then
the run was discarded whole, because nothing was written until the last attempt
finished. A run that had spent twenty minutes and real money left nothing behind.

So the first Ctrl-C is a **cooperative stop**, not an exception: it kills the
agents in flight, refuses to start any further attempt, and lets the pool drain
so the results can be assembled and saved. The default handler is restored
immediately, so a second Ctrl-C is still the hard quit it always was.

Three consequences worth naming:

- **A partial run is an ordinary run file.** Every metric already divides by
  *usable* attempts rather than by k
  ([0007](0007-raw-success-rate-is-the-primary-metric.md)), so a short sample
  scores correctly without a special case. Nothing downstream needs to know.
- **`interrupted` is recorded, not inferred.** A short attempt list already has
  an innocent cause — `--fail-fast` truncates tasks on purpose — so the two must
  not read alike. Same reasoning as `ablated`
  ([0015](0015-ablation-names-its-subject-at-the-invocation.md)): a semantic
  fact is stated, never sniffed from a schema shape.
- **Attempts the cancellation killed are dropped, not recorded.** A killed agent
  comes back looking like an `infra_error`, but it is the interrupt showing up
  in the sample rather than anything about the skill. Attempts that finished on
  their own are kept; ones the stop broke are discarded exactly like ones that
  never started.

  The two are told apart by *who killed the process*, not by the outcome —
  `cancel` remembers what it signalled, and the flag rides out on the
  `AttemptResult`. Dropping on the outcome instead would delete the very
  evidence you were interrupting: cancel a run during a real throttling storm
  and the storm's own dead attempts are observations, while the ones this
  module killed are artefacts, and both exit non-zero.

A fatal misconfiguration diagnosed mid-run (an expired credential, a CLI that
stopped resolving) travels the same path: it cancels the run, and `RunAborted`
carries the partial results out so the CLI saves them *before* it reports the
cause. The error was already fatal; it should not also be expensive.

## Costs accepted

Killing an agent means owning its process group. Backends spawn with
`start_new_session=True` and cancellation sends `SIGKILL` to the group, which
also fixes a latent leak — a timed-out agent used to orphan the tools it had
spawned. The registry of live processes is process-global state
(`caliper/cancel.py`), deliberately: the signal it answers is process-global,
and threading a token through `HarnessBackend.run` would widen that narrow seam
([0003](0003-cli-agent-backends-only.md)) for a fact no backend varies.

Higher real concurrency also means more upstream throttling, which currently
lands as `infra_error` — unusable attempts that cost money and measure nothing.
Retrying a throttled attempt before classifying it is the natural follow-up, and
until it exists `--workers` should be raised deliberately rather than by habit.

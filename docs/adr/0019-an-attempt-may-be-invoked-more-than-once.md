# An attempt may be invoked more than once

An **attempt** is one measured shot at the task; an **invocation** is one spawn
of the agent. They used to be the same object. They are not, because an
invocation the provider refuses — a 429, an overload, a 503 — produces no
measurement at all, and counting it as an attempt puts the provider's queue
depth into the denominator the score divides by.

So a throttled invocation is retried (twice, backing off 2s then 4s with
jitter), and the invocations are folded back into **one** `AttemptRecord`
carrying a `retries` count.

## `INFRA_ERROR` was three things wearing one label

`looks_like_infra_failure` matched a rate limit, a spending cap, and — via a
bare non-zero exit — an agent crash, and treated all three identically. The
question that separates them is **would another invocation behave differently?**

| Signal | Answer | Response |
|---|---|---|
| 429 / overloaded / 503 / too many requests | Yes, in seconds | Retry, bounded |
| spending cap / quota / usage limit | No, not in this run | Abort the run |
| non-zero exit with no signal in it | No, it reproduces | Record as unusable |

Retrying all three would spin on a cap until the wall clock died and paper over
a crash that reproduces. Retrying none of them is what we had: paid attempts
that measure nothing, and the number of them went **up** when
[0018](0018-the-attempt-is-the-unit-of-parallelism.md) raised real concurrency,
because concurrent agents share one rate limit.

The labels themselves are unchanged. All three still earn `INFRA_ERROR` when
they reach the end of the line; the split only decides what happens *before* the
label is reached.

## A signal only counts when it is the outcome, not the content

The three-way split above is a regex over provider prose, and caliper's whole
job is running agents that write prose. An agent answering a task **about** API
error handling writes "rate limit", "quota exceeded", "503" — and a bare match
would respawn that passing attempt twice, or abort the run on it.

So a match is only honoured when it is what the invocation *produced* rather than
something inside what it produced: the invocation failed (non-zero exit, or no
output at all), or its entire output is short enough to be a bail-out message
rather than a result. That second clause is why the check survives at all — a
capped CLI exits **0** with the cap message as its only output, which is the case
`outcome.py` has always matched on a zero exit.

The pre-existing regex had the same false-positive surface, but the consequence
was one attempt mislabelled `infra_error`. Retrying and aborting raise the stakes
enough that the discriminator has to exist.

A timeout is excluded explicitly rather than by falling through the match, so a
backend that returns partial output alongside a timeout cannot earn a respawn on
the strength of text the agent wrote before it hung.

## A spending cap stops the run

The precedent already existed one line above in `outcome.py`: startup auth
failure raises `HarnessConfigurationError` and aborts, rather than letting every
attempt discover the same broken login. A cap is the same shape — the account is
out of budget and will still be out of budget on attempt 40 — so it takes the
same path, reusing the `RunAborted` salvage from
[0018](0018-the-attempt-is-the-unit-of-parallelism.md): the run stops, what
already ran is saved, and the cap is reported as the cause.

The distinction that matters is not "startup vs mid-run". It is whether the next
invocation could go differently.

## What the merge rule claims

Folding several invocations into one record means deciding what each field
*means*, and each answer is a claim:

- **What happened** — transcript, output, exit code, resolved model — comes from
  the **last** invocation. The earlier ones measured nothing, which is why they
  were retried; keeping their output would report a throttle message as the
  agent's answer.
- **What it cost** — tokens — is **summed**. They were really spent, even by an
  invocation that died holding them. The same reasoning already puts unusable
  attempts' tokens in the run total rather than hiding them
  ([0006](0006-track-token-volume-and-wall-time-not-dollar-cost.md)).
- **How long it took** — `duration_seconds` — is the **sum of the spawns**, and
  excludes the waiting between them. That field is pinned to time inside
  `_execute` (docs/CONTEXT.md → Wall-clock time); folding backoff into it would
  turn a latency figure into a measure of the provider's queue and stop it
  comparing across runs. The `retries` count is what tells a reader the wall
  time was fought for.

## Costs accepted

**`--fail-fast N` costs more spawns than it used to.** It still counts
*attempts*, so `--fail-fast 3` can now mean up to nine invocations. Re-scoping it
to count invocations was rejected: the flag would then mean different things in
different runs, and a throttle-retry that succeeds is not evidence that anything
is broken. The inflation is bounded and stated in the flag's help.

**The retry holds its worker.** Backing off in the worker thread parks it, and
under throttling every worker tends to be backing off at once. Re-queueing would
free the slot for work that would meet the same 429, and would break the
round-robin ordering [0018](0018-the-attempt-is-the-unit-of-parallelism.md)
established — so the slot is held deliberately. Revisit if backoffs ever get long
enough to matter.

**No flags.** The policy is 2 retries at 2s/4s for everyone. A run throttled
harder than that is not throttled — it is rate-limited into next week, and
waiting is not the answer.

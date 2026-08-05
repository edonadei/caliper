# An unpinned git source is allowed because drift is reported, not prevented

`ref:` on a [[skill source|git source]] is **optional**. Omitted, it means the
repo's default branch — which will move. Caliper allows that because every fetch
resolves the ref to a concrete commit and records it, and `caliper compare`
reports [[skill drift]] when a member's content differs between two saved runs.
The pin is a convenience, not the guarantee; the guarantee is that drift is
*loud*.

## Context: strictness lands where it is cheap

The obvious design is a mandatory 40-character commit SHA — drift becomes
impossible. It was rejected because adding a competitor to a neighbourhood is
already a chore nobody performs (every spec in this repo has a one-member
neighbourhood), and requiring the author to go dig up a commit hash to add one
makes the chore worse in exactly the place the feature needs it to get easier.

The usual counter-argument to permissiveness does not apply here. The tension
worth respecting is that a strict pin makes the everyday *edit skill, re-run*
loop painful — but the skill under iteration is a **path source**, a file on
disk, and is never pinned at all. Whatever strictness we choose lands only on
members the author is deliberately holding fixed. That is what makes the
permissive default affordable rather than lazy: the cost of being wrong is a
warning on a comparison, not a corrupted loop.

There is also a natural incentive without a rule. A commit-pinned entry is fully
offline after its first fetch; an unpinned one costs one `git ls-remote` per run
to re-resolve. Authors pin the members they care about because it is faster.

## Drift is graded by provenance, not by role

`compare` reports drift for every member, but not at the same volume: a **git
source** that moved warns, a **path source** that moved is reported without
alarm. A future reader will reasonably ask whether that privileges one entry —
so, explicitly, it does not, and the reason is not the one it might look like.

The distinction is **not** "the skill under test versus its neighbours". That
framing would reopen what [0014](0014-activation-is-a-check-type-not-a-separate-command.md)
and [0015](0015-ablation-names-its-subject-at-the-invocation.md) settled:
`skills:` is a list of peers, subjecthood is a runtime axis, and no entry is
privileged. Every member is a member of the neighbourhood, and the vocabulary
stays flat.

What differs is **whether the spec made a claim caliper could keep**. A git
source says where its bytes come from, in the file, in a form caliper can
reproduce; when its content moves between runs, a promise the spec made did not
hold, and the reader is looking at a confounded delta. A path source makes no
such claim — it is whatever is on disk at run time, by construction — so its
content moving is not a broken promise and warning about it would be telling the
author their own edit is a mistake. Crucially, provenance is a property of the
entry and survives a reorder, which is the specific failure mode 0014 rejected
positional subjecthood to avoid.

Two cases this gets right that a subject/neighbour split would get wrong: a
git-sourced skill the author is actively iterating on still warns (correctly —
they took the reproducibility claim on), and a spec whose members are all path
sources still reports every one of them instead of going silent.

## Considered options

- **Warn on any member whose content moved, grade nothing.** Honest, and
  rejected: it fires on every single iteration of the core loop, so an author
  learns to skip the warning line — and thereby skips the confounding case too.
  A warning trained past is worse than no warning.
- **Warn only on git-source drift, say nothing about path sources.** Simplest,
  but a spec with two path sources gets nothing at all, and the fact that a
  member's text moved is worth *showing* even when it is expected.
- **Refuse a drifted comparison rather than warn.** Rejected on the precedent
  `_check_era` sets: the hard stop is reserved for a diff that is "meaningless
  and looks entirely normal". Drift is legible — both commits can be printed —
  which puts it with `k_mismatch` and `neighbourhood_mismatch`, not with the era
  boundary.

## Consequences

- **A missing member refuses the run; a stale one warns and proceeds.** If a git
  source cannot be fetched and is not cached, the run stops. This follows
  `apply_ablation`'s reasoning about an unknown `--ablate` name: proceeding
  "would produce a full run recorded and labelled as an ablation, which is a
  plausible-looking number with nothing in the output to invite suspicion", and
  a run with a member silently absent is that same failure — activation
  precision measured against competition that was not there. But an unpinned
  entry that is cached and merely cannot be *re-resolved* (no network) runs on
  the cache and says so: a stale member is fully auditable, because its commit
  lands in the snapshot and `compare` will report the drift the moment it
  matters. Refusing there would block the local loop for a network reason
  unrelated to what is being measured.
- **`compare` reads the snapshot hashes for the first time.** They have been
  recorded per file since the snapshots went plural and nothing has ever read
  them — `_neighbourhood` compares only `s.name`, so today `compare` catches a
  change in *membership* and is blind to a change in *text*.
- **An unpinned entry costs one network round trip per run** even on a warm
  cache, because the ref must be re-resolved to know whether the cache is
  current. A commit-pinned entry costs none.

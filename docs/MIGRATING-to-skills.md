# Migrating an existing `.eval.yaml` to `skills:`

Caliper no longer force-loads a skill. Every declared skill is **installed**
where the agent looks for skills, and the agent decides whether to use it — so
"did it fire?" became measurable, and `skill:` became `skills:`.

This is a mechanical migration for the common case, but there are four traps a
find-and-replace will miss. Work through the checklist in order.

Background: [ADR 0013](adr/0013-install-and-discover-is-the-only-loading-discipline.md),
[ADR 0014](adr/0014-activation-is-a-check-type-not-a-separate-command.md).

---

## 1. Replace the `skill:` block

```yaml
# before
skill:
  path: ./SKILL.md

# after
skills:
  - ./SKILL.md
```

Entries are **bare path strings**. A bare-agent eval drops the block entirely
(`skill: {}` becomes nothing at all).

`caliper validate` catches this one for you — running it on an unmigrated spec
prints the replacement.

## 2. Check that every skill is a real `SKILL.md`

Each entry must be a file named `SKILL.md`, in its own directory, with
frontmatter `name:` and `description:`. The **name is the identity**: caliper
installs at `<skills_root>/<name>/`, and that is what `activates:` matches on —
not the filename, not the directory.

A **lone slash-command `.md`** (e.g. `~/.claude/commands/review.md`) is no longer
testable and `validate` rejects it: with no name and no description there is
nothing for an agent to discover. Point at the skill's real `SKILL.md`, or drop
the entry. (Follow-up for invoking slash commands: issue #78.)

## 3. Grep your prompts, `expect:` and `assert:` for the old shape ⚠️

**This is the trap.** Step 1 only rewrites a YAML key. If your spec is an eval
*of a skill that writes caliper specs*, the dead format is also sitting inside
your strings, where no schema migration can see it:

```bash
grep -rn 'skill\.path\|skill:\s*$\|\["skill"\]\|top-level skill block' *.eval.yaml
```

Three places to fix:

- **`prompt:`** — a prompt that says *"the spec must have a top-level skill
  block with skill.path"* now instructs the agent to produce a spec caliper
  rejects. The task fails, and worse, it teaches the wrong format.
- **`expect:`** — the autorater grades against the criterion you wrote, so a
  stale one grades the wrong thing.
- **`assert:`** — `spec["skill"]["path"]` raises `KeyError` on a correct spec.
  Use `spec["skills"][0]`.

Specs **embedded in `setup:` heredocs** count too — they get validated at run
time like any other.

## 4. Stop naming the skill in prompts

Under install-and-discover, a prompt like *"Use the grill-skill to write…"*
removes the very choice being measured: the skill fires because you told it to,
not because its `description` worked. Write the prompt a real user would:

```yaml
# before
prompt: I want to improve my eval using grill-skill. …

# after
prompt: I want to improve the eval for my skill at /tmp/x/SKILL.md. …
```

## 5. Add `activates:` (optional, but it is the point)

```yaml
tasks:
  - name: Happy path
    prompt: …
    expect: …
    activates: [my-skill]        # exactly this fired, nothing else

  - name: Unrelated work — silence expected
    prompt: Rename `x` to `y` across the repo.
    activates: []                # a trigger probe: no judge, no execution score
```

`activates:` asserts the **exact set**. A skill that delegates to another must
declare that other skill in `skills:` and enumerate the chain
(`activates: [mine, helper]`) — an undeclared skill is never installed, so it can
never activate, and `validate` will reject an `activates:` naming one.

A task with `activates:` and no `expect:`/`assert:` is a **trigger probe**: it
skips the judge entirely (much cheaper) and reports as `trigger only`, not a
zero.

---

## After migrating

```bash
caliper validate my-skill.eval.yaml   # catches 1, 2 and the activates: names
caliper run my-skill.eval.yaml --k 1  # catches 3 and 4 — the string-level ones
```

`--k 1` first is deliberate: steps 3 and 4 only surface when an agent actually
runs, and a `k=1` run is cheap enough to iterate on.

**Your old saved runs are not comparable to new ones**, and `caliper compare`
will refuse to diff across the boundary. Before this change, `claude-code`
measured invocation × execution under a mangled skill name while the other
backends measured execution with the skill force-loaded — neither is what a run
measures now. Re-run the older side rather than trusting the delta.

Expect scores to move, in both directions. The execution score is now a **joint**
measurement of "did the agent reach for the skill" × "did the skill work", so a
skill with a good body and a weak `description` will drop — and the `activated`
column tells you which half moved.

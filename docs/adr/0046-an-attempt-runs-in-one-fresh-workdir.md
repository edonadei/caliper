# An attempt runs in one fresh workdir

An attempt used to spread across three directories: the agent ran in its
isolated home, `assert:` in the spec's directory, and `setup:`/`cleanup:`
wherever `caliper` was launched (#130). A relative path meant something
different to each step, so an assertion could never see what the agent wrote,
`setup:` could never prepare anything the agent would see, and hooks wrote into
the user's shell directory.

Each attempt now gets one fresh, empty directory — the **attempt workdir** —
created beside the isolated home in the attempt's temp dir, and deleted with it.
`setup:`, the agent, `assert:`, the autorater (and its script-mode check) and
`cleanup:` all run there. Hooks and assertions also get two environment
variables: `CALIPER_WORKDIR` and `CALIPER_SPEC_DIR`.

## Not the spec directory

Running every step in the spec's directory was the other candidate. It loses on
three counts:

- attempts run in parallel (docs/adr/0018), and would write over each other's
  files in a shared directory;
- the agent would work beside the spec file, the answer key the sandbox forbids;
- whatever the agent left behind would land in the user's repository, and the
  next attempt would start from it.

## Not the isolated home itself

The home holds the agent's CLI config, credentials and installed skills. Using
it as the working tree would put that config into every `ls`, `git status` and
relative glob the agent or an assertion runs. A sibling directory keeps the
working tree to what `setup:` put there.

## Empty, with no fixture field

The workdir starts empty and is not a git repository. A task that needs files or
a repo says so in `setup:` — e.g. `cp -R "$CALIPER_SPEC_DIR/fixture/." .` or
`git init` — rather than through a new spec field. A declared fixture can be
added later without changing where anything runs; removing a field is harder
than adding one.

`assert: ./check.py` still resolves against the spec's directory: it names a
file in the spec, not in the workdir. It only *runs* in the workdir.

## A retried invocation keeps the workdir

A throttled invocation is retried inside the same attempt (docs/adr/0019), in
the same workdir and without re-running `setup:`. That is safe because a retry
only follows an invocation that never answered, so it left nothing behind; the
isolated home is reused the same way. Resetting the workdir would mean re-running
`setup:` per invocation, for a case that has no state to reset.

## Hooks keep the caller's environment

Hooks and assertions keep the caller's own environment (real `HOME`, `PATH`),
as before. They are the spec author's code, not the agent under test.

# A run is addressed by its timestamp

A saved run's **run id** is its UTC timestamp, formatted `%Y-%m-%dT%H-%M-%SZ`.
That string is the file's name, the value `report --run` takes, and the handle
`list` prints in its Run column.

The alternative was an opaque id — a counter, or a hash of the run's content —
with the timestamp kept inside the file where it already lives.

## What the timestamp buys

**Ordering is free.** The format sorts lexicographically, so "that spec's latest
run" is `sorted(glob("*.json"))[-1]` — no file needs opening to find it. This is
load-bearing well beyond convenience: a bare spec name standing in for a run
reference (`caliper report my-skill`, `caliper compare a b`) only works because
resolving "latest" is cheap enough to do on every invocation, and `list` renders
its table in run order without reading anything it doesn't already have to.

**The id is legible.** A caller reading `list` output can tell which run is which
without opening any of them, which matters most for the case the ids exist to
serve: an [[ablation]] pair, two runs of one spec in one directory, where the
control arm has to be named by path (docs/CONTEXT.md → Run comparison). An
opaque id would make that a lookup.

## What it costs

**Two runs of one spec in the same second collide**, and the second silently
overwrites the first. In practice a run is many agent invocations long, so this
needs deliberate effort to hit — but it is a real hole, and the fix if it ever
lands is a disambiguating suffix rather than a different scheme, because
everything above depends on the prefix still sorting.

**The id is not content-addressed**, so two runs cannot be recognised as
identical, and a run's id says nothing about what produced it. Neither is
something caliper asks of an id: runs are compared by `diff_runs` on their
recorded contents, never by identity.

**Hand-editing a results file cannot change its id** without renaming the file
too, since the timestamp is stored in both places. Nothing reconciles them; the
name wins for addressing and the field wins for display.

## Where this is implemented

`caliper/runstore.py` — `RUN_ID_FORMAT`, `RunStore.run_id`, and `RunStore.latest`.
It is the only module that knows the layout, so this decision has exactly one
site to revisit.

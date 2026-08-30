# Saved runs live at a discovered results root

Every command — the one that writes runs and the three that read them —
resolves the same **results root** by calling `RunStore.discover()`: the nearest
`.caliper/` at or above the working directory, bounded by the enclosing git
repository. When no root exists yet, one is named at the repository root
(falling back to the working directory outside a repository).

Before this, `run` alone rooted its store at the *spec file's parent*, so
results landed beside the spec. `report` and `compare` rooted theirs at the
working directory, and `list` at `--dir` (default `.`). Those agree only when
you happen to invoke caliper from the spec's own directory; anywhere else a run
was written where nothing would look for it, and `caliper report <spec>`
answered "No results found" for a run that existed on disk. The consolidation in
`caliper/runstore.py` concentrated the layout but deliberately left the root
alone, because it is a question about where results *belong* rather than about
who knows the path.

## Why a root, and why discovered

**Results are a property of the project, not of the spec file.** A run's value is
comparative — `compare` diffs two of them, `list` ranks a project's specs side by
side (docs/CONTEXT.md → Saved run). Filing runs beside each spec scatters that
history across every folder a spec happens to live in: a repo with `evals/`,
`skills/foo/`, and a root-level spec would grow three unrelated `.caliper/`
directories, and no single `list` could show them together.

**The reader addresses a run without knowing where its spec is.** A run reference
is a spec *name* (docs/CONTEXT.md → Saved run), not a path. Keeping results
beside the spec would mean `report my-skill` had to find `my.eval.yaml` first —
by searching the tree, or by making every caller pass a spec path where a name
works today.

**Discovery is what users already expect.** Walking up for a marker directory is
how `git`, `npm`, `pytest` and `ruff` all behave. Rooting at the working
directory alone would be simpler to state, but it keeps the failure this record
exists to remove: `caliper report my-skill` from `evals/` in the same project
that just ran it would still come up empty. Discovery removes the failure rather
than relocating it.

## The rules, and why each one

**Nearest wins.** A package inside a monorepo that has been given its own
`.caliper/` keeps its own eval history rather than pooling it with the repo's.
Two packages with independent eval suites should not share a `list`.

**The walk stops at the git repository.** Without a boundary, a stray `.caliper/`
in `$HOME` would silently become the store for every repo below it — a failure
that is invisible until someone wonders why two projects' runs are interleaved.
This puts a git dependency in the resolution path, which is not otherwise about
git; caliper already reads git for skill snapshots, so the repo boundary is not
a foreign concept here. Outside a repository, discovery falls back to the
working directory.

**A missing root is named at the repo root, not at the working directory.**
Otherwise the *first* `caliper run` of a project plants the root wherever the
caller happened to be standing, and every later run from elsewhere in the same
project creates a second one. Nothing about that is visible until a `report`
comes up empty — which is this record's original bug, re-created on day one.

**`mkdir .caliper` is the escape hatch.** The marker is the control: creating one
in a subdirectory makes it a results root, and nearest-wins does the rest.

## What this costs

**`list --dir` is removed outright**, not deprecated. With discovery it pointed
the listing at a root that `report` and `compare` could not follow, which is the
asymmetry this record removes; and the escape hatch above covers the case it
served. This departs from the precedent in
[[0015-ablation-names-its-subject-at-the-invocation]], where `--baseline` was
kept parseable for one release so an outside caller would get a real
explanation instead of typer's bare "No such option". The departure is
deliberate: `--dir` is a reader's convenience with an obvious substitute (work
from the project), where `--baseline` silently changed what a scripted caller
paid for and what it measured.

**Where a run lands depends on the enclosing project**, so the same spec run
from two checkouts keeps two histories. That is the intended reading of
"results belong to the project", but it does mean a run is not addressable from
outside the project that produced it, except by path.

**Runs already filed beside a spec are not migrated.** They are still found when
caliper is invoked from that spec's directory, since the walk finds
`evals/.caliper/` immediately — so the change reads as "runs moved root", not
"runs vanished". Moving them is a `mv` into the project's root; a run file is
self-describing, so relocating it changes nothing about how it reads back.

**The sandbox now forbids a *shape*, not a path.** `SpecSandbox.from_spec`
resolved `RunStore(spec_path.parent).caliper_dir` into its `auto` list, which
this record makes impossible: a discovered root is not a path a single call can
resolve, and the one it picked would have been forbidden while every other stayed
readable — an older root, a monorepo sibling's, another checkout's.
`sandbox.SAVED_RESULTS` is a real regex applied on every run, alongside the
escaped `auto` paths rather than among them. The rule is now stated as it was
always meant: an agent under test never reads a caliper results directory,
wherever it is.

The pattern deliberately matches `.caliper/results/` rather than `.caliper`
alone. The cheat detector inspects every string in an attempt's tool input, so a
bare marker flags a task that legitimately *writes a caliper spec* — grill-skill's
own eval writes specs whose text contains `./.caliper/.*`, and that spec file
carries a comment recording the false cheat flag it caused when the pattern was
listed by hand. The narrower form keeps the answer key forbidden while leaving
"writes about caliper" alone.

## Alternatives considered

**Root at the working directory, no discovery.** One line in each caller and no
walk, but `report` from a subdirectory of the project still fails, so the
mismatch moves rather than closes.

**Always the repository root, ignoring any nearer `.caliper/`.** Simpler to state
and immune to the coin flip, but wrong for a monorepo whose packages have
independent eval suites.

**A `--results-dir` flag or a project config file.** Answers the question by
declining to: every caller still has to pass the same value, and a mismatched
flag reproduces exactly this bug with more surface area. A default would still
have to be chosen, and it would be this one.

**Results beside the spec, with readers resolving spec names by search.** The
honest version of "results belong to the spec", but it makes every read a
filesystem walk, gives ambiguous answers when two specs share a name, and still
cannot produce a single cross-project `list`.

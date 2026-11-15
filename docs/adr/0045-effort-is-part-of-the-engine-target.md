# Effort is part of the engine target

Every backend CLI takes a per-invocation effort level (`--effort`, `--thinking`,
`--reasoning`, `--variant`, codex's `model_reasoning_effort`), and the level
changes both the pass rate and the token cost. Before this, an attempt ran at
whatever the copied user config pinned, `--no-user-customizations` dropped it
silently, and no run recorded it, so two runs at different efforts read as the
same engine in `compare`.

We name the effort in the engine target, after `@`:
`[backend][:model][@effort]`, for `--model` and `--judge-model` alike. It is part
of the engine, so it follows the engine's rules (docs/adr/0004): no spec key,
and recorded in `RunMeta` as `effort`, `effort_source` and `judge_effort`.

- `@` is split off only before a known effort word, or after a bare backend
  name, so a Vertex-style model id like `claude-sonnet-4@20250514` keeps its
  `@`.
- Each backend takes its own CLI's words, declared statically in
  `caliper/backends.py` (`None` for codex and opencode, which check their own).
  We don't map one vocabulary onto all of them: the levels don't line up, and a
  mapped level would record an effort the CLI never saw.
- pi's own `<model>:<level>` shorthand is refused. It would reach pi without
  the run recording it, and `@` is the one spelling.
- The recorded effort is what the CLI wrote down when it writes one (codex's
  rollout, pi's stream, opencode's and hermes' session stores), else the named
  level, else the `effortLevel` the copied claude-code settings pin.
  `effort_source` says which. A reported level that differs from the named one
  wins and warns, as a reported model does.
- The judge follows the `--model` backend, not its effort, and records only the
  level it was given: it runs in the real CLI home, which caliper does not read
  back.

## Considered options

- A `--effort` and `--judge-effort` flag pair. More discoverable, but two more
  flags, and one engine split across three of them.
- `:` as the separator, as pi spells it. It clashes with the `backend:model`
  split.
- `/` or `+`. `/` clashes with `provider/model` on pi, opencode and hermes.

## Consequences

- `compare` shows the effort in the engine row; a different effort is a
  different engine, like a different model.
- Antigravity reports no level and no confirmed settings key for one, so with
  no `@` its run records no effort.
- An effort word caliper doesn't know, after a model, stays part of the model.
  An opencode variant outside the known words can still be named after a bare
  `opencode@`, but not after a model.

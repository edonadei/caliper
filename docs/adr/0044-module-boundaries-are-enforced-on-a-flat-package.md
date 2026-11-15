# Module boundaries are enforced on a flat package

Caliper is one bounded context ([`docs/CONTEXT.md`](../CONTEXT.md)), so most
modules sit side by side at the top of `caliper/` rather than under `domain/`,
`runtime/` and `cli/` subpackages. Left unchecked, a flat package gives no
hint about which imports cross a seam: a backend module gets imported eagerly
outside `get_harness`, a subprocess or `runner` dependency creeps into scoring
or attempt policy, or `rich` and `typer` leak below the CLI edge.

We keep the flat `caliper/` layout and enforce the import graph in
`tests/test_architecture.py` (backed by Ruff `TID251` on registered backend
modules in `BACKENDS` and on the internal modules of the `harness` and `judge`
seams):

- **Registry and config** (`caliper`, `backends`, `config`) import Python
  stdlib only, and **schemas** (`schema/*`) add only `pydantic`, `yaml` and
  `backends`.
- **Pure scoring and diffing** (`compare`, `activation`) stay free of
  filesystem, process, thread, `runner` and CLI imports; **attempt policies**
  (`attempt`, `sandbox`, `retry`) stay subprocess-, thread-, `runner`- and
  CLI-free over injected seams (`AttemptWorkdir`, `HarnessBackend`, `Judge`,
  `RunStore`).
- **Execution, harnesses and judges** (`runner`, `environment`, `workdir`,
  `cancel`, `runstore`, `skills*`, `chatgpt`, `harness/*`, `judge/*`) never
  import `commands/*`, `reporter`, `main`, `telemetry`, `rich`, `typer` or
  `click`, and registered backend modules never import one another or load
  eagerly from `caliper.harness`.
- **CLI and presentation** (`main`, `commands/*`, `reporter`, `telemetry`) sit
  at the top; `reporter` renders `schema.results` without importing `commands`,
  `runner`, `harness` or `typer`.
- **Seam internals** stay inside their package: outside `caliper/harness/`,
  code imports only `harness`, `harness.base`, `harness.mcp` and
  `harness.refusal`; outside `caliper/judge/`, only `judge` and `judge.base`.

Splitting `caliper/` into `domain/`, `runtime/` and `cli/` subpackages would
make those tiers visible in `ls`, but Caliper has one domain vocabulary, not
several bounded contexts, and moving eighteen modules churns `git blame` and
every in-flight branch for a boundary an AST test checks in under half a
second.

## Consequences

- Every `caliper/*.py` module must be classified in `tests/test_architecture.py`;
  adding an unclassified module fails the suite until its boundary is named.
- Adding a backend to `BACKENDS` fails `tests/test_architecture.py` until its
  module is also listed under Ruff's `banned-api` table in `pyproject.toml`.
- Adding a module to `caliper/harness/` or `caliper/judge/` fails
  `tests/test_architecture.py` until it is banned under `banned-api` or listed
  as part of that seam's public surface.

# The agent and a step are spawned under different rules

The agent under test is spawned by `CliHarness._execute`, and every
[[step]] by `AttemptWorkdir._run`. Both tag the process, track it for
cancellation, and kill it at a deadline, so they look like one loop written
twice. They are kept apart on purpose, because they disagree about **when a
process is finished**:

- **The agent is finished when its output pipes close.** An agent's tools can
  outlive its CLI. A detached tool that still holds stdout keeps the attempt open
  until the timeout, and is then killed with the rest of the agent's tree. An
  attempt never returns while something it started is still running.
- **A step is finished when its own process exits.** `setup: ./server &` is a
  normal thing to write: the background server is meant to outlive the hook and
  serve the agent. Waiting for its pipe to close would time every such setup
  out, and killing it would break it.

A shared module would need a knob for this, plus one for each difference that
follows from it: separate stdout and stderr in full versus merged output as a
bounded tail, a cancellation returned versus raised, stdin from a staged prompt
file versus inherited. That is a shallow module with two adapters' worth of
parameters. Folding both into one loop would instead change what an attempt's
outcome means. [0029](0029-every-step-runs-under-the-same-rules.md) governs
steps only. The agent is not a step (docs/CONTEXT.md → Step).

The pinned behaviour lives in `tests/test_cancel.py` (the agent's pipe holders)
and `tests/test_workdir.py` (steps).

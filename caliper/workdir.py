"""The attempt workdir: where the agent, its hooks and its assertions all run.

One fresh directory per attempt, shared by ``setup:``, the agent, ``assert:``
and ``cleanup:``, so a relative path means the same place to every step. See
docs/adr/0026-an-attempt-runs-in-one-fresh-workdir.md.
"""

from __future__ import annotations

import os

#: The attempt workdir, for a step that has changed directory.
WORKDIR_ENV = "CALIPER_WORKDIR"
#: The spec's own directory, where an author keeps fixtures to copy in.
SPEC_DIR_ENV = "CALIPER_SPEC_DIR"


def step_env(workdir: str, spec_dir: str) -> dict[str, str]:
    """The environment a hook or assertion runs with: the caller's, plus both dirs.

    Deliberately the caller's own environment rather than the agent's isolated
    one: hooks and assertions are the spec author's code, not the agent under
    test, and have always run with the developer's ``HOME`` and ``PATH``.
    """
    return {**os.environ, WORKDIR_ENV: workdir, SPEC_DIR_ENV: spec_dir}

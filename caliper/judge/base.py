from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from caliper.harness.base import ConversationTurn
from caliper.schema.spec import TaskSpec
from caliper.workdir import AttemptWorkdir


@dataclass
class JudgeResult:
    passed: bool
    reasoning: str
    assert_passed: bool | None = None
    assert_evidence: str | None = None
    autorater_passed: bool | None = None
    autorater_reasoning: str | None = None
    # True when the judge could not produce any usable verdict (unparseable
    # autorater response, or the judge call threw). Distinct from a `passed`
    # verdict of False, which is a genuine task failure.
    errored: bool = False
    # The concrete model the autorater used, when the judge CLI reports it (e.g.
    # claude-code echoes it in its JSON output). ``None`` when no LLM autorater
    # ran (assert-only task) or the backend does not surface the model.
    resolved_model: str | None = None
    # Wall-clock seconds the LLM autorater took. ``None`` when none ran, which
    # is the difference between "the judge was fast" and "no judge ran"
    # (docs/CONTEXT.md → Judge time).
    autorater_seconds: float | None = None


class Judge(Protocol):
    """The judge contract: what the runner depends on to grade an attempt.

    A structural seam, not a family — there is one production implementation
    (``EvalJudge``); test doubles conform by shape. Backend variation lives in
    ``HarnessBackend.run_prompt`` (see PR #61), not here.

    ``backend`` and ``model`` are the judge engine as configured — what
    ``RunMeta`` records, asked of the judge rather than passed in beside it.
    ``model`` is ``None`` when the judge lets its CLI pick.

    ``workdir`` is the attempt workdir, where every assertion *runs*. An
    ``assert: ./check.py`` path still resolves from the spec's directory, which
    the workdir carries as ``workdir.spec_dir``
    (docs/adr/0026-an-attempt-runs-in-one-fresh-workdir.md).
    """

    backend: str
    model: str | None

    def evaluate(
        self,
        task: TaskSpec,
        transcript: list[ConversationTurn],
        final_output: str,
        workdir: AttemptWorkdir,
    ) -> JudgeResult: ...

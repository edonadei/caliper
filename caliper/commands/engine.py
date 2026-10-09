"""Which engine runs the skill and which grades it, from ``--model`` and
``--judge-model``, and whether that judge can run on this machine.

The engine is a runtime axis, not a spec field (docs/CONTEXT.md → Engine as
runtime axis, docs/adr/0004). The judge follows the skill's backend unless
``--judge-model`` names one (docs/adr/0034).
"""

from __future__ import annotations

from dataclasses import dataclass

from caliper.commands.diagnosis import CannotRun
from caliper.harness import get_harness
from caliper.harness.base import HarnessBackend
from caliper.judge import EvalJudge
from caliper.schema.spec import DEFAULT_BACKEND, VALID_BACKENDS, EvalSpec, parse_target


@dataclass(frozen=True)
class Engine:
    """The resolved ``(backend, model)`` pairs, the ones ``RunMeta`` records.

    ``judge_named`` is whether ``--judge-model`` chose the judge, which decides
    what a missing judge CLI should tell the caller to change.
    """

    backend: str
    model: str | None
    judge_backend: str
    judge_model: str | None
    judge_named: bool

    def harness(self) -> HarnessBackend:
        """The backend that runs the skill."""
        return get_harness(self.backend, self.model)

    def judge(self) -> EvalJudge:
        """The judge, which builds its own backend from the same pair."""
        return EvalJudge(self.judge_backend, self.judge_model)


def resolve_engine(model: str | None, judge_model: str | None) -> Engine:
    """Resolve the two flags, refusing an unknown backend before any attempt.

    A misspelt backend would otherwise surface as a traceback (``--model``) or
    as a ``judge_error`` on every attempt after the agent was already paid for
    (``--judge-model``).
    """
    backend, skill_model = DEFAULT_BACKEND, None
    if model:
        b, skill_model = parse_target(model)
        backend = b or backend

    # Only the backend is followed, not the skill's model: a judge on its CLI's
    # own default grades --model codex:<cheap model> as well as --model codex. A
    # bare --judge-model reads like a bare --model: a claude-code model.
    judge_backend, judge_model_name = backend, None
    if judge_model:
        jb, judge_model_name = parse_target(judge_model)
        judge_backend = jb or DEFAULT_BACKEND

    for flag, chosen in (("--model", backend), ("--judge-model", judge_backend)):
        if chosen not in VALID_BACKENDS:
            raise CannotRun(
                f"Unknown backend {chosen!r} in {flag}.\n\n"
                f"Known backends: {', '.join(sorted(VALID_BACKENDS))}.\n"
                f"Pass {flag} <backend>[:<model>], e.g. {flag} codex:gpt-5-codex, "
                f"or a bare model name for the default {DEFAULT_BACKEND} backend.",
                title="Unknown backend",
            )

    return Engine(
        backend=backend,
        model=skill_model,
        judge_backend=judge_backend,
        judge_model=judge_model_name,
        judge_named=judge_model is not None,
    )


def check_judge_cli(engine: Engine, spec: EvalSpec) -> None:
    """Refuse an ``expect:`` spec whose judge CLI is not installed here.

    Otherwise every graded attempt pays for the agent and then lands as a
    ``judge_error``. An assert-only spec never calls the judge's CLI.
    """
    if not any(t.expect for t in spec.tasks):
        return
    if not get_harness(engine.judge_backend, engine.judge_model).prompt_cli_missing():
        return
    raise CannotRun(_judge_cli_missing(engine), title="No judge")


def _judge_cli_missing(engine: Engine) -> str:
    """Why the ``expect:`` checks cannot be graded here, and the ways out.

    Only a judge that ``--judge-model`` named stays put when ``--model``
    changes, and only then is removing the flag a way out, unless the
    ``--model`` backend is the same missing CLI.
    """
    judge = engine.judge_backend
    if not engine.judge_named:
        return (
            f"The {judge} CLI isn't installed. It would run the agent "
            f"(--model) and, with no --judge-model, grade the `expect:` checks "
            "too.\n\n"
            f"Install and sign in to the {judge} CLI, or pick an "
            "installed one with --model."
        )
    way_out = (
        "point --judge-model at an installed backend"
        if judge == engine.backend
        else f"remove --judge-model and {engine.backend} (your --model) will grade too"
    )
    return (
        f"--judge-model {judge} asks {judge} to grade the "
        f"`expect:` checks, but the {judge} CLI isn't installed.\n\n"
        f"Install and sign in to the {judge} CLI, or {way_out}."
    )

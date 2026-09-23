"""Grading ``classify:`` checks with Jev (docs/adr/0027).

Each authored check becomes one Choice question. Checks that share an evidence
view share one request, so the evidence is sent (and paid for) once, and each
answer comes back under the check's own authored name. The typed answer then
becomes a verdict by fixed rules. Nothing here generates or infers reasoning.
"""

from __future__ import annotations

from typing import Callable

from caliper.harness.base import ConversationTurn
from caliper.judge import evidence
from caliper.judge.jev import ChoiceQuestion, JevError, JevResponse, ask_choices
from caliper.schema.results import ClassificationRecord, ClassificationVerdict
from caliper.schema.spec import ClassifyCheck

# The evidence projection behind each ``evidence:`` value.
_VIEWS: dict[str, Callable[[str, list[ConversationTurn], str], dict]] = {
    "tool_trace": evidence.tool_trace,
}

Ask = Callable[..., JevResponse]


def _question(check: ClassifyCheck) -> ChoiceQuestion:
    return ChoiceQuestion(instructions=check.question, criteria=dict(check.choices))


def _record(check: ClassifyCheck, **fields) -> ClassificationRecord:
    return ClassificationRecord(
        name=check.name,
        evidence=check.evidence,
        require=check.require,
        abstain=check.abstain,
        min_probability=check.min_probability,
        **fields,
    )


def _verdict(check: ClassifyCheck, selected: str, probability: float) -> tuple:
    """(verdict, error) for one answer, by the rules in docs/adr/0027."""
    if selected == check.abstain:
        return ClassificationVerdict.ERROR, f"abstained ({check.abstain})"
    if probability < check.min_probability:
        return (
            ClassificationVerdict.ERROR,
            f"uncertain: {selected} at p={probability:.2f} is below "
            f"min_probability {check.min_probability:.2f}",
        )
    if selected == check.require:
        return ClassificationVerdict.PASS, None
    return ClassificationVerdict.FAIL, None


def evaluate_classify(
    checks: list[ClassifyCheck],
    *,
    task_prompt: str,
    transcript: list[ConversationTurn],
    final_output: str,
    ask: Ask = ask_choices,
) -> list[ClassificationRecord]:
    """One record per check, in authored order."""
    by_view: dict[str, list[ClassifyCheck]] = {}
    for check in checks:
        by_view.setdefault(check.evidence, []).append(check)

    records: dict[str, ClassificationRecord] = {}
    for view, group in by_view.items():
        state = _VIEWS[view](task_prompt, transcript, final_output)
        try:
            response = ask(state, {c.name: _question(c) for c in group})
        except JevError as err:
            for check in group:
                records[check.name] = _record(
                    check,
                    verdict=ClassificationVerdict.ERROR,
                    error=f"{err.kind.value}: {err.message}",
                )
            continue
        for check in group:
            answer = response.answers[check.name]
            verdict, error = _verdict(check, answer.choice, answer.probability)
            records[check.name] = _record(
                check,
                verdict=verdict,
                selected=answer.choice,
                probabilities=answer.probabilities,
                model=response.model,
                latency_seconds=response.latency_seconds,
                request_bytes=response.request_bytes,
                error=error,
            )
    return [records[check.name] for check in checks]

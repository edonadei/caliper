"""``--ablate``: removing a declared skill for one run (docs/adr/0015).

Covers the four seams the ADR names — the reduced install, the dropped
activation expectation, the explicit ``RunMeta.ablated`` marker, and ``compare``
recognising an ablation pair from that marker rather than sniffing neighbourhood
shapes.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from caliper.compare import diff_runs
from caliper.harness.base import AttemptResult, ConversationTurn, HarnessBackend
from caliper.judge.base import JudgeResult
from caliper.runner import run
from caliper.schema.results import (
    ERA_INSTALL_AND_DISCOVER,
    AggregateScore,
    AttemptRecord,
    Outcome,
    RunMeta,
    RunResults,
    SkillSnapshot,
    TaskResult,
)
from caliper.schema.spec import EvalSpec, TaskSpec
from caliper.skills import SkillResolutionError


# --- fixtures -------------------------------------------------------------


class RecordingHarness(HarnessBackend):
    """Passes every attempt, remembering which skills it was asked to install."""

    def __init__(self) -> None:
        self.installed: list[list[str]] = []

    @property
    def name(self) -> str:
        return "recording"

    def run(
        self,
        task_id,
        attempt,
        prompt,
        *,
        skill_refs,
        model,
        timeout,
        isolated_home,
        extra_path=None,
        mcp_servers=None,
        forbidden_files=None,
    ) -> AttemptResult:
        self.installed.append([ref.name for ref in skill_refs])
        return AttemptResult(
            task_id=task_id,
            attempt=attempt,
            transcript=[
                ConversationTurn(
                    role="tool_use",
                    content="[tool: Read]",
                    tool_name="Read",
                    tool_input={"file_path": f"{isolated_home}/skills/keeper/SKILL.md"},
                )
            ],
            final_output="done",
            exit_code=0,
            duration_seconds=0.1,
        )


class PassingJudge:
    def evaluate(self, *args, **kwargs) -> JudgeResult:
        return JudgeResult(passed=True, reasoning="ok")


def _spec_with_two_skills(tmp_path, *, activates=None) -> tuple[EvalSpec, object]:
    paths = []
    for name in ("subject", "keeper"):
        d = tmp_path / name
        d.mkdir()
        (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---\nbody")
        paths.append(str(d / "SKILL.md"))
    spec_path = tmp_path / "s.eval.yaml"
    spec_path.write_text("tasks: []\n")
    spec = EvalSpec(
        skills=paths,
        tasks=[
            TaskSpec(
                id="task-001",
                name="t",
                prompt="p",
                expect="anything",
                activates=activates,
            )
        ],
    )
    return spec, spec_path


def _run_spec(spec, spec_path, harness, **kwargs) -> RunResults:
    return run(
        spec=spec,
        spec_path=spec_path,
        harness=harness,
        judge=PassingJudge(),
        k=1,
        workers=1,
        timeout=30,
        **kwargs,
    )


# --- the reduced install --------------------------------------------------


def test_ablate_installs_the_neighbourhood_minus_the_named_skill(tmp_path):
    spec, spec_path = _spec_with_two_skills(tmp_path)
    harness = RecordingHarness()
    _run_spec(spec, spec_path, harness, ablate=["subject"])
    assert harness.installed == [["keeper"]]


def test_ablating_every_member_leaves_the_bare_agent(tmp_path):
    spec, spec_path = _spec_with_two_skills(tmp_path)
    harness = RecordingHarness()
    _run_spec(spec, spec_path, harness, ablate=["subject", "keeper"])
    assert harness.installed == [[]]


def test_a_run_without_ablate_installs_everything(tmp_path):
    spec, spec_path = _spec_with_two_skills(tmp_path)
    harness = RecordingHarness()
    results = _run_spec(spec, spec_path, harness)
    assert harness.installed == [["subject", "keeper"]]
    assert results.run.ablated == []


# --- the explicit marker --------------------------------------------------


def test_the_ablated_names_are_recorded_on_run_meta(tmp_path):
    # An empty skill_snapshots list is otherwise ambiguous between "ablated
    # everything" and "declared no skills" — the marker is what disambiguates.
    spec, spec_path = _spec_with_two_skills(tmp_path)
    results = _run_spec(spec, spec_path, RecordingHarness(), ablate=["subject"])
    assert results.run.ablated == ["subject"]


def test_snapshots_cover_only_the_installed_skills(tmp_path):
    # A snapshot claims "this is what produced the score"; the ablated skill did
    # not, because it was never installed.
    spec, spec_path = _spec_with_two_skills(tmp_path)
    results = _run_spec(spec, spec_path, RecordingHarness(), ablate=["subject"])
    assert [s.name for s in results.skill_snapshots] == ["keeper"]


def test_a_repeated_name_is_recorded_once(tmp_path):
    # The marker is the run's own description of what it did, so it should not
    # read `ablated: subject, subject` for one removed skill.
    spec, spec_path = _spec_with_two_skills(tmp_path)
    results = _run_spec(
        spec, spec_path, RecordingHarness(), ablate=["subject", "subject"]
    )
    assert results.run.ablated == ["subject"]


def test_ablating_an_undeclared_skill_is_refused(tmp_path):
    # A typo would otherwise produce a full run labelled as an ablation.
    spec, spec_path = _spec_with_two_skills(tmp_path)
    with pytest.raises(SkillResolutionError) as exc:
        _run_spec(spec, spec_path, RecordingHarness(), ablate=["subjekt"])
    assert "subjekt" in str(exc.value)


# --- activation under ablation --------------------------------------------


def test_an_ablated_run_drops_the_activation_expectation(tmp_path):
    # Filtering `activates: [subject, keeper]` down to `[keeper]` would have
    # caliper assert a claim the author never wrote — and with a delegating
    # parent removed, its neighbours correctly stop firing.
    spec, spec_path = _spec_with_two_skills(tmp_path, activates=["subject", "keeper"])
    results = _run_spec(spec, spec_path, RecordingHarness(), ablate=["subject"])
    task = results.task_results[0]
    assert task.activation_expected is None
    assert task.activation_score is None


def test_an_activates_naming_the_ablated_skill_is_not_a_validation_error(tmp_path):
    # The expectation is dropped, not violated: validation still sees the full
    # declared set, so a normal spec stays runnable under --ablate.
    spec, spec_path = _spec_with_two_skills(tmp_path, activates=["subject"])
    results = _run_spec(spec, spec_path, RecordingHarness(), ablate=["subject"])
    assert results.task_results[0].activation_expected is None


def test_the_observation_survives_even_though_the_verdict_does_not(tmp_path):
    # (c) from the design: observe, don't score. The transcript reads keeper's
    # installed SKILL.md, and that fact is still recorded.
    spec, spec_path = _spec_with_two_skills(tmp_path, activates=["subject", "keeper"])
    results = _run_spec(spec, spec_path, RecordingHarness(), ablate=["subject"])
    attempt = results.task_results[0].attempts[0]
    assert attempt.activated == ["keeper"]
    assert attempt.activation_passed is None


def test_a_normal_run_still_scores_its_activation_expectation(tmp_path):
    spec, spec_path = _spec_with_two_skills(tmp_path, activates=["keeper"])
    results = _run_spec(spec, spec_path, RecordingHarness())
    assert results.task_results[0].activation_expected == ["keeper"]
    assert results.task_results[0].activation_score == 1.0


# --- compare recognises an ablation pair ----------------------------------


def _saved(*, skills: list[str], ablated: list[str]) -> RunResults:
    return RunResults(
        run=RunMeta(
            spec="demo",
            timestamp=datetime(2026, 8, 1, tzinfo=timezone.utc),
            k=3,
            backend="claude-code",
            era=ERA_INSTALL_AND_DISCOVER,
            ablated=ablated,
        ),
        skill_snapshots=[
            SkillSnapshot(name=n, path=f"/x/{n}/SKILL.md") for n in skills
        ],
        task_results=[
            TaskResult(
                task_id="task-001",
                task_name="shared",
                attempts=[
                    AttemptRecord(
                        attempt=1, output="", duration_seconds=1.0, outcome=Outcome.PASS
                    )
                ],
                successes=1,
                unusable=0,
                pass_at_k=1.0,
            )
        ],
        aggregate=AggregateScore(avg_score=1.0, per_task=[]),
    )


def test_an_ablation_pair_is_labelled_and_not_warned_about():
    # The differing neighbourhood *is* the experiment, so the generic warning
    # would be describing the design as a mistake.
    a = _saved(skills=["keeper"], ablated=["subject"])
    b = _saved(skills=["keeper", "subject"], ablated=[])
    comp = diff_runs(a, b)
    assert comp.neighbourhood_mismatch is False
    assert comp.warnings == []
    assert comp.a_label == "without subject"
    assert comp.b_label == "full neighbourhood"


def test_a_bare_agent_pair_is_labelled_as_such():
    a = _saved(skills=[], ablated=["subject", "keeper"])
    b = _saved(skills=["keeper", "subject"], ablated=[])
    comp = diff_runs(a, b)
    assert comp.a_label == "bare agent"
    assert comp.neighbourhood_mismatch is False


def test_the_ablated_side_is_recognised_in_either_position():
    a = _saved(skills=["keeper", "subject"], ablated=[])
    b = _saved(skills=["keeper"], ablated=["subject"])
    comp = diff_runs(a, b)
    assert comp.a_label == "full neighbourhood"
    assert comp.b_label == "without subject"
    assert comp.neighbourhood_mismatch is False


def test_two_runs_that_ablated_different_skills_still_warn():
    # Not an ablation pair. Nothing but the marker could catch this: both sides
    # have a smaller-than-declared neighbourhood, which no shape comparison
    # distinguishes from the legitimate case.
    a = _saved(skills=["keeper"], ablated=["subject"])
    b = _saved(skills=["subject"], ablated=["keeper"])
    comp = diff_runs(a, b)
    assert comp.neighbourhood_mismatch is True
    assert any("neighbourhood" in w for w in comp.warnings)
    assert comp.a_label is None


def test_an_inconsistent_marker_is_not_treated_as_a_pair():
    # Claims to have ablated `subject`, but the neighbourhoods do not agree with
    # that claim — fall back to the generic warning rather than trusting it.
    a = _saved(skills=["keeper"], ablated=["subject"])
    b = _saved(skills=["keeper", "subject", "rival"], ablated=[])
    comp = diff_runs(a, b)
    assert comp.neighbourhood_mismatch is True
    assert comp.a_label is None


def test_two_full_runs_are_unlabelled():
    a = _saved(skills=["keeper"], ablated=[])
    b = _saved(skills=["keeper"], ablated=[])
    comp = diff_runs(a, b)
    assert comp.a_label is None and comp.b_label is None
    assert comp.warnings == []

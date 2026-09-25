"""``--ablate``: removing a declared skill or MCP server for one run.

Covers the seams the ADRs name — the reduced install, the reduced MCP config, the
dropped activation expectation (skills only), the explicit ``RunMeta.ablated``
marker, and ``compare`` recognising an ablation pair from that marker rather than
sniffing neighbourhood shapes. See docs/adr/0015 and docs/adr/0025.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from caliper.compare import diff_runs
from caliper.harness.base import (
    AttemptResult,
    ConversationTurn,
    RunContext,
)
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
from caliper.schema.spec import EvalSpec, McpServer, TaskSpec
from caliper.skills import AblationError, SkillResolutionError

from conftest import ScriptedHarness, ScriptedJudge, agent_result


# --- fixtures -------------------------------------------------------------


def _reads_a_skill(ctx: RunContext) -> AttemptResult:
    """Read one installed skill's file, so activation has something to observe
    whatever the spec declared."""
    target = ctx.skill_refs[-1].name if ctx.skill_refs else "none"
    return agent_result(
        transcript=[
            ConversationTurn(
                role="tool_use",
                content="[tool: Read]",
                tool_name="Read",
                tool_input={
                    "file_path": f"{ctx.isolated_home}/skills/{target}/SKILL.md"
                },
            )
        ]
    )


def _recording() -> ScriptedHarness:
    """Passes every attempt, remembering the skills and MCP servers it was given."""
    return ScriptedHarness(_reads_a_skill, supports_mcp=True)


def _installed(harness: ScriptedHarness) -> list[list[str]]:
    return [[ref.name for ref in ctx.skill_refs] for ctx in harness.contexts]


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


def _spec_with_skill_and_server(
    tmp_path, *, activates=None, collide=False
) -> tuple[EvalSpec, object]:
    """A spec declaring a `subject` skill plus a `weather` MCP server.

    ``collide`` also declares a skill named `weather`, so the bare name is
    ambiguous and only a qualifier can name a side.
    """
    subject = tmp_path / "subject"
    subject.mkdir()
    (subject / "SKILL.md").write_text("---\nname: subject\ndescription: d\n---\nbody")
    paths = [str(subject / "SKILL.md")]
    if collide:
        weather = tmp_path / "weather"
        weather.mkdir()
        (weather / "SKILL.md").write_text(
            "---\nname: weather\ndescription: d\n---\nbody"
        )
        paths.append(str(weather / "SKILL.md"))
    spec_path = tmp_path / "s.eval.yaml"
    spec_path.write_text("tasks: []\n")
    spec = EvalSpec(
        skills=paths,
        mcp={"weather": McpServer(command="python3", args=["w.py"])},
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
        judge=ScriptedJudge(),
        k=1,
        workers=1,
        timeout=30,
        **kwargs,
    )


# --- the reduced install --------------------------------------------------


def test_ablate_installs_the_neighbourhood_minus_the_named_skill(tmp_path):
    spec, spec_path = _spec_with_two_skills(tmp_path)
    harness = _recording()
    _run_spec(spec, spec_path, harness, ablate=["subject"])
    assert _installed(harness) == [["keeper"]]


def test_ablating_every_member_leaves_the_bare_agent(tmp_path):
    spec, spec_path = _spec_with_two_skills(tmp_path)
    harness = _recording()
    _run_spec(spec, spec_path, harness, ablate=["subject", "keeper"])
    assert _installed(harness) == [[]]


def test_a_run_without_ablate_installs_everything(tmp_path):
    spec, spec_path = _spec_with_two_skills(tmp_path)
    harness = _recording()
    results = _run_spec(spec, spec_path, harness)
    assert _installed(harness) == [["subject", "keeper"]]
    assert results.run.ablated == []


# --- the explicit marker --------------------------------------------------


def test_the_ablated_names_are_recorded_on_run_meta(tmp_path):
    # An empty skill_snapshots list is otherwise ambiguous between "ablated
    # everything" and "declared no skills" — the marker is what disambiguates.
    spec, spec_path = _spec_with_two_skills(tmp_path)
    results = _run_spec(spec, spec_path, _recording(), ablate=["subject"])
    assert results.run.ablated == ["subject"]
    assert results.run.ablated_skills == ["subject"]
    assert results.run.ablated_servers == []


def test_snapshots_cover_only_the_installed_skills(tmp_path):
    # A snapshot claims "this is what produced the score"; the ablated skill did
    # not, because it was never installed.
    spec, spec_path = _spec_with_two_skills(tmp_path)
    results = _run_spec(spec, spec_path, _recording(), ablate=["subject"])
    assert [s.name for s in results.skill_snapshots] == ["keeper"]


def test_a_repeated_name_is_recorded_once(tmp_path):
    # The marker is the run's own description of what it did, so it should not
    # read `ablated: subject, subject` for one removed skill.
    spec, spec_path = _spec_with_two_skills(tmp_path)
    results = _run_spec(spec, spec_path, _recording(), ablate=["subject", "subject"])
    assert results.run.ablated == ["subject"]


def test_ablating_an_undeclared_skill_is_refused(tmp_path):
    # A typo would otherwise produce a full run labelled as an ablation.
    spec, spec_path = _spec_with_two_skills(tmp_path)
    with pytest.raises(SkillResolutionError) as exc:
        _run_spec(spec, spec_path, _recording(), ablate=["subjekt"])
    assert "subjekt" in str(exc.value)


# --- ablating an MCP server -----------------------------------------------


def test_ablate_removes_a_declared_mcp_server(tmp_path):
    # The server never reaches the harness config, so the agent never sees its
    # tool definitions — which is the question being asked of it.
    spec, spec_path = _spec_with_skill_and_server(tmp_path)
    harness = _recording()
    _run_spec(spec, spec_path, harness, ablate=["weather"])
    # An empty mapping, not None: the block was declared, so the backend must
    # still isolate the attempt to zero servers rather than fall back to its own
    # ambient config (see test_mcp.py for the runner-level guard).
    assert [ctx.mcp_servers for ctx in harness.contexts] == [{}]
    assert _installed(harness) == [["subject"]]


def test_a_run_without_ablate_hands_over_every_server(tmp_path):
    spec, spec_path = _spec_with_skill_and_server(tmp_path)
    harness = _recording()
    _run_spec(spec, spec_path, harness)
    assert [ctx.mcp_servers for ctx in harness.contexts] == [
        {"weather": McpServer(command="python3", args=["w.py"])}
    ]


def test_the_ablated_server_is_recorded_qualified_on_run_meta(tmp_path):
    # The marker names what was removed; mcp_servers records the environment the
    # run actually had (none), so the saved run describes both sides of it.
    spec, spec_path = _spec_with_skill_and_server(tmp_path)
    results = _run_spec(spec, spec_path, _recording(), ablate=["weather"])
    assert results.run.ablated == ["mcp:weather"]
    assert results.run.ablated_skills == []
    assert results.run.ablated_servers == ["weather"]
    assert results.run.mcp_servers == []


def test_the_servers_a_run_kept_are_recorded_on_run_meta(tmp_path):
    spec, spec_path = _spec_with_skill_and_server(tmp_path)
    results = _run_spec(spec, spec_path, _recording())
    assert results.run.ablated == []
    assert results.run.mcp_servers == ["weather"]


def test_a_bare_name_on_a_collision_is_refused(tmp_path):
    spec, spec_path = _spec_with_skill_and_server(tmp_path, collide=True)
    with pytest.raises(AblationError) as exc:
        _run_spec(spec, spec_path, _recording(), ablate=["weather"])
    message = str(exc.value)
    assert "mcp:weather" in message and "skill:weather" in message
    assert exc.value.title == "Invalid ablation"


def test_the_mcp_qualifier_removes_the_server_not_the_skill(tmp_path):
    spec, spec_path = _spec_with_skill_and_server(tmp_path, collide=True)
    harness = _recording()
    results = _run_spec(spec, spec_path, harness, ablate=["mcp:weather"])
    assert _installed(harness) == [["subject", "weather"]]
    assert [ctx.mcp_servers for ctx in harness.contexts] == [{}]
    assert results.run.ablated == ["mcp:weather"]


def test_the_skill_qualifier_removes_the_skill_not_the_server(tmp_path):
    spec, spec_path = _spec_with_skill_and_server(tmp_path, collide=True)
    harness = _recording()
    results = _run_spec(spec, spec_path, harness, ablate=["skill:weather"])
    assert _installed(harness) == [["subject"]]
    assert [ctx.mcp_servers for ctx in harness.contexts] == [
        {"weather": McpServer(command="python3", args=["w.py"])}
    ]
    assert results.run.ablated == ["weather"]


def test_ablating_an_undeclared_server_is_refused(tmp_path):
    spec, spec_path = _spec_with_skill_and_server(tmp_path)
    with pytest.raises(SkillResolutionError) as exc:
        _run_spec(spec, spec_path, _recording(), ablate=["mcp:snow"])
    assert "mcp:snow" in str(exc.value)


def test_repeating_a_server_name_records_one_removal(tmp_path):
    spec, spec_path = _spec_with_skill_and_server(tmp_path)
    results = _run_spec(
        spec, spec_path, _recording(), ablate=["weather", "mcp:weather"]
    )
    assert results.run.ablated == ["mcp:weather"]


def test_ablating_a_server_leaves_the_activation_expectation_scored(tmp_path):
    # `activates:` names skills, and every one is still installed, so a server
    # ablation has no reason to withhold the verdict.
    spec, spec_path = _spec_with_skill_and_server(tmp_path, activates=["subject"])
    results = _run_spec(spec, spec_path, _recording(), ablate=["weather"])
    task = results.task_results[0]
    assert task.activation_expected == ["subject"]
    assert task.activation_score == 1.0


# --- activation under ablation --------------------------------------------


def test_an_ablated_run_drops_the_activation_expectation(tmp_path):
    # Filtering `activates: [subject, keeper]` down to `[keeper]` would have
    # caliper assert a claim the author never wrote — and with a delegating
    # parent removed, its neighbours correctly stop firing.
    spec, spec_path = _spec_with_two_skills(tmp_path, activates=["subject", "keeper"])
    results = _run_spec(spec, spec_path, _recording(), ablate=["subject"])
    task = results.task_results[0]
    assert task.activation_expected is None
    assert task.activation_score is None


def test_an_activates_naming_the_ablated_skill_is_not_a_validation_error(tmp_path):
    # The expectation is dropped, not violated: validation still sees the full
    # declared set, so a normal spec stays runnable under --ablate.
    spec, spec_path = _spec_with_two_skills(tmp_path, activates=["subject"])
    results = _run_spec(spec, spec_path, _recording(), ablate=["subject"])
    assert results.task_results[0].activation_expected is None


def test_the_observation_survives_even_though_the_verdict_does_not(tmp_path):
    # (c) from the design: observe, don't score. The transcript reads keeper's
    # installed SKILL.md, and that fact is still recorded.
    spec, spec_path = _spec_with_two_skills(tmp_path, activates=["subject", "keeper"])
    results = _run_spec(spec, spec_path, _recording(), ablate=["subject"])
    attempt = results.task_results[0].attempts[0]
    assert attempt.activated == ["keeper"]
    assert attempt.activation_passed is None


def test_a_normal_run_still_scores_its_activation_expectation(tmp_path):
    spec, spec_path = _spec_with_two_skills(tmp_path, activates=["keeper"])
    results = _run_spec(spec, spec_path, _recording())
    assert results.task_results[0].activation_expected == ["keeper"]
    assert results.task_results[0].activation_score == 1.0


# --- compare recognises an ablation pair ----------------------------------


def _saved(
    *, skills: list[str], ablated: list[str], mcp_servers: list[str] | None = None
) -> RunResults:
    return RunResults(
        run=RunMeta(
            spec="demo",
            timestamp=datetime(2026, 8, 1, tzinfo=timezone.utc),
            k=3,
            backend="claude-code",
            era=ERA_INSTALL_AND_DISCOVER,
            ablated=ablated,
            mcp_servers=mcp_servers or [],
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


def test_a_server_only_ablation_is_labelled_from_the_marker():
    # The skill neighbourhood is unchanged, so the label rests on the removed
    # server: the marker names it, and the full side's recorded membership
    # confirms it was there to remove.
    a = _saved(skills=["keeper"], ablated=["mcp:weather"], mcp_servers=[])
    b = _saved(skills=["keeper"], ablated=[], mcp_servers=["weather"])
    comp = diff_runs(a, b)
    assert comp.neighbourhood_mismatch is False
    assert comp.warnings == []
    assert comp.a_label == "without mcp:weather"
    assert comp.b_label == "full neighbourhood"


def test_ablating_a_server_is_not_a_bare_agent_when_a_server_survives():
    # No skills, two servers, one ablated: the surviving server means the ablated
    # side is not the bare agent, whatever the skill neighbourhood says.
    a = _saved(skills=[], ablated=["mcp:first"], mcp_servers=["second"])
    b = _saved(skills=[], ablated=[], mcp_servers=["first", "second"])
    comp = diff_runs(a, b)
    assert comp.a_label == "without mcp:first"
    assert comp.b_label == "full neighbourhood"


def test_ablating_every_skill_is_not_a_bare_agent_when_a_server_survives():
    # The label describes what actually ran, tools included.
    a = _saved(skills=[], ablated=["subject"], mcp_servers=["weather"])
    b = _saved(skills=["subject"], ablated=[], mcp_servers=["weather"])
    comp = diff_runs(a, b)
    assert comp.a_label == "without subject"


def test_a_legacy_side_without_recorded_servers_is_not_called_bare():
    # An unrecorded membership is unknown, not "no servers": the honest label
    # names what was ablated rather than claiming nothing was configured.
    a = _saved(skills=[], ablated=["subject"])
    a.run.mcp_servers = None
    b = _saved(skills=["subject"], ablated=[])
    comp = diff_runs(a, b)
    assert comp.a_label == "without subject"


def test_a_legacy_run_without_recorded_servers_still_pairs():
    # A run saved before mcp_servers existed reads as "not recorded", not "ran
    # with none", so it can still be the full side of an ablation pair.
    cut = _saved(skills=["keeper"], ablated=["subject"], mcp_servers=[])
    full = _saved(skills=["keeper", "subject"], ablated=[])
    full.run.mcp_servers = None
    comp = diff_runs(cut, full)
    assert comp.a_label == "without subject"
    assert comp.b_label == "full neighbourhood"


def test_a_server_marker_the_full_side_never_had_is_not_a_pair():
    # The spec dropped the server between the two runs, so both ran without it
    # and the delta is not the server's. The marker alone would have claimed it.
    a = _saved(skills=["keeper"], ablated=["mcp:weather"])
    b = _saved(skills=["keeper"], ablated=[], mcp_servers=[])
    comp = diff_runs(a, b)
    assert comp.a_label is None and comp.b_label is None
    assert comp.neighbourhood_mismatch is False


def test_a_server_marker_the_ablated_side_still_ran_with_is_not_a_pair():
    # Claims to have removed a server it recorded running with — an inconsistent
    # marker, not an ablation.
    a = _saved(skills=["keeper"], ablated=["mcp:weather"], mcp_servers=["weather"])
    b = _saved(skills=["keeper"], ablated=[], mcp_servers=["weather"])
    comp = diff_runs(a, b)
    assert comp.a_label is None


def test_a_server_difference_between_two_skill_ablated_runs_is_not_a_pair():
    # The marker explains the skill it named; it must not paper over a changed
    # MCP environment as well.
    a = _saved(skills=["keeper"], ablated=["subject"], mcp_servers=["weather"])
    b = _saved(skills=["keeper", "subject"], ablated=[], mcp_servers=[])
    comp = diff_runs(a, b)
    assert comp.a_label is None


def test_different_recorded_servers_warn():
    # Same skills, different tool environment: the score can move for a reason
    # unrelated to the skill, so say so instead of presenting the delta bare.
    a = _saved(skills=["keeper"], ablated=[], mcp_servers=["weather"])
    b = _saved(skills=["keeper"], ablated=[], mcp_servers=[])
    comp = diff_runs(a, b)
    assert comp.mcp_mismatch is True
    assert any("MCP servers" in w for w in comp.warnings)
    assert comp.a_label is None and comp.b_label is None


def test_matching_recorded_servers_do_not_warn():
    a = _saved(skills=["keeper"], ablated=[], mcp_servers=["weather"])
    b = _saved(skills=["keeper"], ablated=[], mcp_servers=["weather"])
    comp = diff_runs(a, b)
    assert comp.mcp_mismatch is False
    assert comp.warnings == []


def test_an_unrecorded_membership_does_not_warn():
    # "Not recorded" is unknown, not "none": warning on it would fire for every
    # comparison against a run saved before the field existed.
    a = _saved(skills=["keeper"], ablated=[], mcp_servers=["weather", "other"])
    b = _saved(skills=["keeper"], ablated=[])
    b.run.mcp_servers = None
    comp = diff_runs(a, b)
    assert comp.mcp_mismatch is False
    assert comp.warnings == []


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

"""``--inherit-mcp``: an attempt keeps the MCP setup its CLI loads by itself.

Covers the run seam (threading the flag, clearing it on a backend without MCP,
recording what was inherited), ``compare`` (warnings and ablation pairing) and
the report header. The per-backend mechanics live beside each harness's tests.
See docs/adr/0028-inherit-mcp-is-an-opt-in-invocation-flag.md.
"""

from __future__ import annotations

import io
from datetime import datetime, timezone

import pytest
from rich.console import Console

import caliper.reporter as reporter_mod
from caliper.compare import diff_runs
from caliper.harness.base import (
    AttemptResult,
    HarnessBackend,
    HarnessConfigurationError,
    RunContext,
)
from caliper.judge.base import JudgeResult
from caliper.runner import run
from caliper.schema.results import (
    ERA_INSTALL_AND_DISCOVER,
    AggregateScore,
    RunMeta,
    RunResults,
    SkillSnapshot,
)
from caliper.schema.spec import EvalSpec, McpServer, TaskSpec, load_spec


class InheritingHarness(HarnessBackend):
    """Passes every attempt and reports a fixed inherited set when asked to."""

    def __init__(self, *, supports_mcp: bool = True, inherited=("gmail",)) -> None:
        self.supports_mcp = supports_mcp
        self.inherited = inherited
        self.contexts: list[RunContext] = []

    @property
    def name(self) -> str:
        return "inheriting"

    def run(self, ctx: RunContext) -> AttemptResult:
        self.contexts.append(ctx)
        return AttemptResult(
            transcript=[],
            final_output="done",
            exit_code=0,
            duration_seconds=0.1,
            inherited_mcp_servers=(
                list(self.inherited)
                if ctx.inherit_mcp and self.inherited is not None
                else None
            ),
        )


class PassingJudge:
    backend = "test"
    model = None

    def evaluate(self, *args, **kwargs) -> JudgeResult:
        return JudgeResult(passed=True, reasoning="ok")


def _spec(tmp_path, *, mcp=None, requires=False):
    spec_path = tmp_path / "s.eval.yaml"
    spec_path.write_text("tasks: []\n")
    fields = {
        "tasks": [TaskSpec(id="task-001", name="t", prompt="p", expect="x")],
        "requires_inherited_mcp": requires,
    }
    if mcp is not None:
        fields["mcp"] = mcp
    return EvalSpec(**fields), spec_path


def _run(tmp_path, harness, *, k=1, mcp=None, requires=False, **kwargs) -> RunResults:
    spec, spec_path = _spec(tmp_path, mcp=mcp, requires=requires)
    return run(
        spec=spec,
        spec_path=spec_path,
        harness=harness,
        judge=PassingJudge(),
        k=k,
        workers=1,
        timeout=30,
        **kwargs,
    )


# --- the run seam ---------------------------------------------------------


def test_off_by_default(tmp_path):
    harness = InheritingHarness()
    results = _run(tmp_path, harness)
    assert [ctx.inherit_mcp for ctx in harness.contexts] == [False]
    assert results.run.inherit_mcp is False
    assert results.run.inherited_mcp_servers is None


def test_the_flag_reaches_every_attempt_and_is_recorded(tmp_path):
    harness = InheritingHarness()
    results = _run(tmp_path, harness, k=2, inherit_mcp=True)
    assert [ctx.inherit_mcp for ctx in harness.contexts] == [True, True]
    assert results.run.inherit_mcp is True
    assert results.run.inherited_mcp_servers == ["gmail"]


def test_declared_servers_stay_in_mcp_servers_only(tmp_path):
    # Ablation pairing reads mcp_servers as the spec's own set, so the
    # inherited ones never leak into it.
    harness = InheritingHarness()
    results = _run(
        tmp_path,
        harness,
        mcp={"echo": McpServer(command="python3")},
        inherit_mcp=True,
    )
    assert results.run.mcp_servers == ["echo"]
    assert results.run.inherited_mcp_servers == ["gmail"]
    assert harness.contexts[0].mcp_servers == {"echo": McpServer(command="python3")}


def test_ablated_names_still_reach_the_harness(tmp_path):
    # So a backend can keep the user's server of that name out of the run.
    harness = InheritingHarness()
    _run(
        tmp_path,
        harness,
        mcp={"echo": McpServer(command="python3")},
        inherit_mcp=True,
        ablate=["echo"],
    )
    ctx = harness.contexts[0]
    assert ctx.mcp_servers == {}
    assert ctx.mcp_declared_names == frozenset({"echo"})


def test_a_backend_that_cannot_see_them_records_unknown(tmp_path):
    results = _run(tmp_path, InheritingHarness(inherited=None), inherit_mcp=True)
    assert results.run.inherit_mcp is True
    assert results.run.inherited_mcp_servers is None


def test_a_backend_without_mcp_warns_and_records_the_flag_off(tmp_path):
    harness = InheritingHarness(supports_mcp=False)
    warnings: list[str] = []
    results = _run(tmp_path, harness, inherit_mcp=True, on_warning=warnings.append)
    assert [ctx.inherit_mcp for ctx in harness.contexts] == [False]
    assert results.run.inherit_mcp is False
    assert results.run.inherited_mcp_servers is None
    assert len(warnings) == 1 and "no effect" in warnings[0]


def test_a_run_saved_before_the_flag_loads_as_off():
    meta = RunMeta.model_validate(
        {
            "spec": "demo",
            "timestamp": "2026-08-01T00:00:00Z",
            "k": 1,
            "backend": "claude-code",
        }
    )
    assert meta.inherit_mcp is False
    assert meta.inherited_mcp_servers is None


# --- requires_inherited_mcp -----------------------------------------------


def test_a_spec_that_requires_it_is_refused_without_the_flag(tmp_path):
    harness = InheritingHarness()
    with pytest.raises(HarnessConfigurationError, match="--inherit-mcp"):
        _run(tmp_path, harness, requires=True)
    # Refused before any attempt was paid for.
    assert harness.contexts == []


def test_a_spec_that_requires_it_is_refused_on_a_backend_without_mcp(tmp_path):
    harness = InheritingHarness(supports_mcp=False)
    with pytest.raises(HarnessConfigurationError, match="no MCP support"):
        _run(tmp_path, harness, requires=True, inherit_mcp=True)
    assert harness.contexts == []


def test_a_spec_that_requires_it_runs_with_the_flag(tmp_path):
    results = _run(tmp_path, InheritingHarness(), requires=True, inherit_mcp=True)
    assert results.run.inherit_mcp is True


def test_the_requirement_is_read_from_yaml_and_defaults_off(tmp_path):
    required = tmp_path / "r.eval.yaml"
    required.write_text(
        "requires_inherited_mcp: true\n"
        "tasks:\n  - name: t\n    prompt: p\n    expect: x\n"
    )
    plain = tmp_path / "p.eval.yaml"
    plain.write_text("tasks:\n  - name: t\n    prompt: p\n    expect: x\n")
    assert load_spec(required).requires_inherited_mcp is True
    assert load_spec(plain).requires_inherited_mcp is False


# --- compare --------------------------------------------------------------


def _saved(
    *,
    inherit: bool,
    inherited: list[str] | None = None,
    skills=("subject", "keeper"),
    ablated=(),
) -> RunResults:
    return RunResults(
        run=RunMeta(
            spec="demo",
            timestamp=datetime(2026, 8, 1, tzinfo=timezone.utc),
            k=3,
            backend="claude-code",
            era=ERA_INSTALL_AND_DISCOVER,
            ablated=list(ablated),
            mcp_servers=[],
            inherit_mcp=inherit,
            inherited_mcp_servers=inherited,
        ),
        skill_snapshots=[
            SkillSnapshot(name=n, path=f"/x/{n}/SKILL.md") for n in skills
        ],
        task_results=[],
        aggregate=AggregateScore(avg_score=0.0, per_task=[]),
    )


def test_a_flag_mismatch_warns():
    comp = diff_runs(_saved(inherit=False), _saved(inherit=True, inherited=["gmail"]))
    assert comp.inherit_mcp_mismatch is True
    assert any("only B ran with --inherit-mcp" in w for w in comp.warnings)


def test_different_inherited_servers_warn():
    comp = diff_runs(
        _saved(inherit=True, inherited=["gmail"]),
        _saved(inherit=True, inherited=["drive"]),
    )
    assert comp.inherit_mcp_mismatch is True
    assert any("different inherited MCP servers" in w for w in comp.warnings)


def test_matching_or_unknown_inherited_servers_do_not_warn():
    same = diff_runs(
        _saved(inherit=True, inherited=["gmail"]),
        _saved(inherit=True, inherited=["gmail"]),
    )
    unknown = diff_runs(
        _saved(inherit=True, inherited=["gmail"]),
        _saved(inherit=True, inherited=None),
    )
    for comp in (same, unknown):
        assert comp.inherit_mcp_mismatch is False
        assert comp.warnings == []


def test_an_ablation_pair_needs_the_same_flag_state():
    full = _saved(inherit=True, inherited=["gmail"])
    cut = _saved(inherit=False, skills=("keeper",), ablated=("subject",))
    comp = diff_runs(full, cut)
    assert comp.a_label is None and comp.b_label is None
    assert comp.inherit_mcp_mismatch is True


def test_an_ablation_pair_under_the_flag_is_still_labelled():
    full = _saved(inherit=True, inherited=["gmail"])
    cut = _saved(
        inherit=True, inherited=["gmail"], skills=("keeper",), ablated=("subject",)
    )
    comp = diff_runs(full, cut)
    assert (comp.a_label, comp.b_label) == ("full neighbourhood", "without subject")
    assert comp.warnings == []


# --- report ---------------------------------------------------------------


def _render(results: RunResults) -> str:
    buf = io.StringIO()
    orig = reporter_mod.console
    reporter_mod.console = Console(file=buf, highlight=False, markup=False, width=160)
    try:
        reporter_mod.print_results(results)
    finally:
        reporter_mod.console = orig
    return buf.getvalue()


def test_the_report_header_names_the_inherited_servers():
    out = _render(_saved(inherit=True, inherited=["drive", "gmail"]))
    assert "inherited MCP:" in out
    assert "drive, gmail" in out


def test_the_report_header_says_when_the_list_is_unknown():
    assert "not listed by this backend" in _render(_saved(inherit=True))
    assert "none found" in _render(_saved(inherit=True, inherited=[]))


def test_an_isolated_run_has_no_marker():
    assert "inherited MCP" not in _render(_saved(inherit=False))

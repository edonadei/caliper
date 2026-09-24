"""``--user-customizations``: an attempt keeps the MCP setup its CLI loads by itself.

Covers the run seam (threading the flag, clearing it on a backend without MCP,
recording what was loaded), ``compare`` (warnings and ablation pairing) and
the report header. The per-backend mechanics live beside each harness's tests.
See docs/adr/0028-runs-load-user-customizations-by-default.md.
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


class CustomizingHarness(HarnessBackend):
    """Passes every attempt and reports a fixed set of loaded customizations when asked to."""

    def __init__(self, *, supports_mcp: bool = True, loaded=("gmail",)) -> None:
        self.supports_mcp = supports_mcp
        self.loaded = loaded
        self.contexts: list[RunContext] = []

    @property
    def name(self) -> str:
        return "customizing"

    def run(self, ctx: RunContext) -> AttemptResult:
        self.contexts.append(ctx)
        return AttemptResult(
            transcript=[],
            final_output="done",
            exit_code=0,
            duration_seconds=0.1,
            loaded_user_customizations=(
                list(self.loaded)
                if ctx.user_customizations and self.loaded is not None
                else None
            ),
        )


class PassingJudge:
    backend = "test"
    model = None

    def evaluate(self, *args, **kwargs) -> JudgeResult:
        return JudgeResult(passed=True, reasoning="ok")


def _spec(tmp_path, *, mcp=None, spec_setting=None):
    spec_path = tmp_path / "s.eval.yaml"
    spec_path.write_text("tasks: []\n")
    fields = {
        "tasks": [TaskSpec(id="task-001", name="t", prompt="p", expect="x")],
        "user_customizations": spec_setting,
    }
    if mcp is not None:
        fields["mcp"] = mcp
    return EvalSpec(**fields), spec_path


def _run(
    tmp_path, harness, *, k=1, mcp=None, spec_setting=None, **kwargs
) -> RunResults:
    spec, spec_path = _spec(tmp_path, mcp=mcp, spec_setting=spec_setting)
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


def test_on_by_default(tmp_path):
    # Most runs test a skill in the user's own agent (docs/adr/0028).
    harness = CustomizingHarness()
    results = _run(tmp_path, harness)
    assert [ctx.user_customizations for ctx in harness.contexts] == [True]
    assert results.run.user_customizations is True
    assert results.run.loaded_user_customizations == ["gmail"]


def test_isolated_on_request(tmp_path):
    harness = CustomizingHarness()
    results = _run(tmp_path, harness, user_customizations=False)
    assert [ctx.user_customizations for ctx in harness.contexts] == [False]
    assert results.run.user_customizations is False
    assert results.run.loaded_user_customizations is None


def test_the_flag_reaches_every_attempt_and_is_recorded(tmp_path):
    harness = CustomizingHarness()
    results = _run(tmp_path, harness, k=2, user_customizations=True)
    assert [ctx.user_customizations for ctx in harness.contexts] == [True, True]
    assert results.run.user_customizations is True
    assert results.run.loaded_user_customizations == ["gmail"]


def test_declared_servers_stay_in_mcp_servers_only(tmp_path):
    # Ablation pairing reads mcp_servers as the spec's own set, so the
    # loaded customizations never leak into it.
    harness = CustomizingHarness()
    results = _run(
        tmp_path,
        harness,
        mcp={"echo": McpServer(command="python3")},
        user_customizations=True,
    )
    assert results.run.mcp_servers == ["echo"]
    assert results.run.loaded_user_customizations == ["gmail"]
    assert harness.contexts[0].mcp_servers == {"echo": McpServer(command="python3")}


def test_ablated_names_still_reach_the_harness(tmp_path):
    # So a backend can keep the user's server of that name out of the run.
    harness = CustomizingHarness()
    _run(
        tmp_path,
        harness,
        mcp={"echo": McpServer(command="python3")},
        user_customizations=True,
        ablate=["echo"],
    )
    ctx = harness.contexts[0]
    assert ctx.mcp_servers == {}
    assert ctx.mcp_declared_names == frozenset({"echo"})


def test_a_backend_that_cannot_see_them_records_unknown(tmp_path):
    results = _run(tmp_path, CustomizingHarness(loaded=None), user_customizations=True)
    assert results.run.user_customizations is True
    assert results.run.loaded_user_customizations is None


def test_a_backend_without_mcp_warns_and_records_the_flag_off(tmp_path):
    harness = CustomizingHarness(supports_mcp=False)
    warnings: list[str] = []
    results = _run(
        tmp_path, harness, user_customizations=True, on_warning=warnings.append
    )
    assert [ctx.user_customizations for ctx in harness.contexts] == [False]
    assert results.run.user_customizations is False
    assert results.run.loaded_user_customizations is None
    assert len(warnings) == 1 and "no effect" in warnings[0]


def test_the_default_on_a_backend_without_mcp_is_silent(tmp_path):
    # It would otherwise warn on every pi run.
    warnings: list[str] = []
    results = _run(
        tmp_path, CustomizingHarness(supports_mcp=False), on_warning=warnings.append
    )
    assert results.run.user_customizations is False
    assert warnings == []


def test_a_run_saved_before_the_flag_loads_as_off():
    meta = RunMeta.model_validate(
        {
            "spec": "demo",
            "timestamp": "2026-08-01T00:00:00Z",
            "k": 1,
            "backend": "claude-code",
        }
    )
    assert meta.user_customizations is False
    assert meta.loaded_user_customizations is None


# --- the spec's default -----------------------------------------------------


def test_a_spec_can_turn_it_on_by_default(tmp_path):
    harness = CustomizingHarness()
    results = _run(tmp_path, harness, spec_setting=True)
    assert [ctx.user_customizations for ctx in harness.contexts] == [True]
    assert results.run.user_customizations is True
    assert results.run.loaded_user_customizations == ["gmail"]


def test_the_invocation_overrides_the_spec_either_way(tmp_path):
    off = _run(
        tmp_path, CustomizingHarness(), spec_setting=True, user_customizations=False
    )
    on = _run(
        tmp_path, CustomizingHarness(), spec_setting=False, user_customizations=True
    )
    assert off.run.user_customizations is False
    assert off.run.loaded_user_customizations is None
    assert on.run.user_customizations is True


def test_a_spec_default_on_a_backend_without_mcp_warns_and_runs(tmp_path):
    harness = CustomizingHarness(supports_mcp=False)
    warnings: list[str] = []
    results = _run(tmp_path, harness, spec_setting=True, on_warning=warnings.append)
    assert [ctx.user_customizations for ctx in harness.contexts] == [False]
    assert results.run.user_customizations is False
    assert len(warnings) == 1 and "no effect" in warnings[0]


def test_the_spec_field_is_read_from_yaml_and_defaults_unset(tmp_path):
    setting = tmp_path / "r.eval.yaml"
    setting.write_text(
        "user_customizations: true\ntasks:\n  - name: t\n    prompt: p\n    expect: x\n"
    )
    plain = tmp_path / "p.eval.yaml"
    plain.write_text("tasks:\n  - name: t\n    prompt: p\n    expect: x\n")
    assert load_spec(setting).user_customizations is True
    assert load_spec(plain).user_customizations is None


# --- compare --------------------------------------------------------------


def _saved(
    *,
    loads: bool,
    loaded: list[str] | None = None,
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
            user_customizations=loads,
            loaded_user_customizations=loaded,
        ),
        skill_snapshots=[
            SkillSnapshot(name=n, path=f"/x/{n}/SKILL.md") for n in skills
        ],
        task_results=[],
        aggregate=AggregateScore(avg_score=0.0, per_task=[]),
    )


def test_a_flag_mismatch_warns():
    comp = diff_runs(_saved(loads=False), _saved(loads=True, loaded=["gmail"]))
    assert comp.user_customizations_mismatch is True
    message = next(w for w in comp.warnings if w.startswith("only B loaded"))
    # Says how to make the pair comparable, since the first diff against a run
    # saved before the default changed lands here.
    assert "re-run B with --no-user-customizations to match A" in message
    assert "--user-customizations to match" not in message


def test_a_cross_backend_diff_with_user_customizations_warns():
    a = _saved(loads=True, loaded=["gmail"])
    b = _saved(loads=True, loaded=["gmail"])
    b.run.backend = "codex"
    comp = diff_runs(a, b)
    assert comp.cross_backend_user_customizations is True
    assert any("different backends (claude-code vs codex)" in w for w in comp.warnings)


def test_a_cross_backend_diff_gives_one_fix_not_two():
    a = _saved(loads=True, loaded=["gmail"])
    b = _saved(loads=False)
    b.run.backend = "codex"
    comp = diff_runs(a, b)
    assert comp.cross_backend_user_customizations and comp.user_customizations_mismatch
    custom_lines = [w for w in comp.warnings if "customizations" in w]
    assert len(custom_lines) == 1
    assert "re-run both with --no-user-customizations" in custom_lines[0]


def test_a_cross_backend_diff_that_loaded_nothing_does_not_warn():
    a, b = _saved(loads=True, loaded=[]), _saved(loads=True, loaded=[])
    b.run.backend = "codex"
    assert diff_runs(a, b).cross_backend_user_customizations is False


def test_an_ablation_pair_that_loaded_different_setups_is_not_labelled():
    full = _saved(loads=True, loaded=["gmail"])
    cut = _saved(
        loads=True,
        loaded=["gmail", "drive"],
        skills=("keeper",),
        ablated=("subject",),
    )
    comp = diff_runs(full, cut)
    assert comp.a_label is None and comp.b_label is None


def test_an_isolated_cross_backend_diff_is_the_harness_comparison():
    a, b = _saved(loads=False), _saved(loads=False)
    b.run.backend = "codex"
    comp = diff_runs(a, b)
    assert comp.cross_backend_user_customizations is False
    assert comp.warnings == []


def test_different_loaded_customizations_warn():
    comp = diff_runs(
        _saved(loads=True, loaded=["gmail"]),
        _saved(loads=True, loaded=["drive"]),
    )
    assert comp.user_customizations_mismatch is True
    assert any("different user customizations" in w for w in comp.warnings)


def test_matching_or_unknown_customizations_do_not_warn():
    same = diff_runs(
        _saved(loads=True, loaded=["gmail"]),
        _saved(loads=True, loaded=["gmail"]),
    )
    unknown = diff_runs(
        _saved(loads=True, loaded=["gmail"]),
        _saved(loads=True, loaded=None),
    )
    for comp in (same, unknown):
        assert comp.user_customizations_mismatch is False
        assert comp.warnings == []


def test_an_ablation_pair_needs_the_same_flag_state():
    full = _saved(loads=True, loaded=["gmail"])
    cut = _saved(loads=False, skills=("keeper",), ablated=("subject",))
    comp = diff_runs(full, cut)
    assert comp.a_label is None and comp.b_label is None
    assert comp.user_customizations_mismatch is True


def test_an_ablation_pair_under_the_flag_is_still_labelled():
    full = _saved(loads=True, loaded=["gmail"])
    cut = _saved(loads=True, loaded=["gmail"], skills=("keeper",), ablated=("subject",))
    comp = diff_runs(full, cut)
    assert (comp.a_label, comp.b_label) == ("full neighbourhood", "without subject")
    assert comp.warnings == []


@pytest.mark.parametrize("loaded", [["gmail"], None])
def test_an_all_ablated_run_with_customizations_is_not_a_bare_agent(loaded):
    full = _saved(loads=True, loaded=loaded, skills=("subject",))
    cut = _saved(loads=True, loaded=loaded, skills=(), ablated=("subject",))
    comp = diff_runs(full, cut)
    assert comp.b_label == "without subject"


def test_an_all_ablated_run_that_loaded_nothing_is_a_bare_agent():
    full = _saved(loads=True, loaded=[], skills=("subject",))
    cut = _saved(loads=True, loaded=[], skills=(), ablated=("subject",))
    assert diff_runs(full, cut).b_label == "bare agent"


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


def test_the_report_header_names_the_loaded_customizations():
    out = _render(_saved(loads=True, loaded=["drive", "gmail"]))
    assert "user customizations:" in out
    assert "drive, gmail" in out


def test_the_report_header_says_when_the_list_is_unknown():
    assert "not listed by this backend" in _render(_saved(loads=True))
    assert "none found" in _render(_saved(loads=True, loaded=[]))


def test_an_isolated_run_has_no_marker():
    assert "user customizations" not in _render(_saved(loads=False))

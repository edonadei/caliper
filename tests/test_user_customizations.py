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
from caliper.runner import run
from caliper.schema.results import (
    ERA_INSTALL_AND_DISCOVER,
    AggregateScore,
    RunMeta,
    RunResults,
    SkillSnapshot,
)
from caliper.schema.spec import EvalSpec, McpServer, TaskSpec

from conftest import ScriptedHarness, ScriptedJudge, agent_result


def _customizing(*, supports_mcp: bool = True, loaded=("gmail",)) -> ScriptedHarness:
    """Passes every attempt, reporting ``loaded`` as its customizations when asked to load them."""
    return ScriptedHarness(
        lambda ctx: agent_result(
            transcript=[],
            loaded_user_customizations=(
                list(loaded) if ctx.user_customizations and loaded is not None else None
            ),
        ),
        name="customizing",
        supports_mcp=supports_mcp,
    )


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
        judge=ScriptedJudge(),
        k=k,
        workers=1,
        timeout=30,
        **kwargs,
    )


# --- the run seam ---------------------------------------------------------


@pytest.mark.parametrize(
    "flag, spec_setting, expected",
    [
        (None, None, True),  # the default loads them (docs/adr/0028)
        (None, True, True),
        (None, False, False),
        (False, None, False),
        (False, True, False),  # the invocation wins, either way
        (True, False, True),
    ],
)
def test_the_invocation_then_the_spec_then_the_default(
    tmp_path, flag, spec_setting, expected
):
    harness = _customizing()
    results = _run(
        tmp_path, harness, k=2, spec_setting=spec_setting, user_customizations=flag
    )
    assert [ctx.user_customizations for ctx in harness.contexts] == [expected] * 2
    assert results.run.user_customizations is expected
    assert results.run.loaded_user_customizations == (["gmail"] if expected else None)


def test_declared_and_ablated_servers_stay_the_specs(tmp_path):
    # mcp_servers stays the spec's own set, which ablation pairing reads; an
    # ablated name still reaches the harness so it can keep a user's server of
    # that name out of the run.
    harness = _customizing()
    mcp = {"echo": McpServer(command="python3"), "gone": McpServer(command="x")}
    results = _run(tmp_path, harness, mcp=mcp, ablate=["gone"])
    assert results.run.mcp_servers == ["echo"]
    assert results.run.loaded_user_customizations == ["gmail"]
    assert harness.contexts[0].spec_mcp_names == frozenset({"echo", "gone"})


def test_a_backend_that_cannot_see_them_records_unknown(tmp_path):
    results = _run(tmp_path, _customizing(loaded=None))
    assert results.run.user_customizations is True
    assert results.run.loaded_user_customizations is None


@pytest.mark.parametrize(
    "flag, spec_setting, warned",
    [(True, None, True), (None, True, True), (None, None, False)],
)
def test_a_backend_without_mcp_runs_isolated_and_warns_only_when_asked(
    tmp_path, flag, spec_setting, warned
):
    # Silent under the default: it would otherwise warn on every pi run.
    harness = _customizing(supports_mcp=False)
    warnings: list[str] = []
    results = _run(
        tmp_path,
        harness,
        spec_setting=spec_setting,
        user_customizations=flag,
        on_warning=warnings.append,
    )
    assert [ctx.user_customizations for ctx in harness.contexts] == [False]
    assert results.run.user_customizations is False
    assert results.run.loaded_user_customizations is None
    assert len(warnings) == (1 if warned else 0)


def test_a_run_saved_before_the_field_loads_as_isolated():
    meta = RunMeta.model_validate(
        {"spec": "demo", "timestamp": "2026-08-01T00:00:00Z", "k": 1, "backend": "x"}
    )
    assert meta.user_customizations is False
    assert meta.loaded_user_customizations is None


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
        aggregate=AggregateScore(avg_score=0.0),
    )


def test_a_flag_mismatch_warns_with_the_isolating_fix():
    comp = diff_runs(_saved(loads=False), _saved(loads=True, loaded=["gmail"]))
    assert comp.user_customizations_mismatch is True
    # The first diff against a run saved before the default changed lands
    # here; only the isolating direction always works.
    assert any(
        "only B loaded" in w and "re-run B with --no-user-customizations" in w
        for w in comp.warnings
    )


@pytest.mark.parametrize(
    "a_loaded, b_loaded, mismatch",
    [
        (["gmail"], ["drive"], True),
        (["gmail"], ["gmail"], False),
        (["skill:personal"], ["rules:AGENTS.md"], True),
        (
            ["mcp:gmail", "settings:config.toml"],
            ["settings:config.toml", "mcp:gmail"],
            False,
        ),
        (["gmail"], ["mcp:gmail"], True),
        (["gmail"], None, False),
    ],
)
def test_loaded_sets_warn_only_when_both_are_known_and_differ(
    a_loaded, b_loaded, mismatch
):
    comp = diff_runs(
        _saved(loads=True, loaded=a_loaded), _saved(loads=True, loaded=b_loaded)
    )
    assert comp.user_customizations_mismatch is mismatch
    assert bool(comp.warnings) is mismatch


@pytest.mark.parametrize(
    "a, b, warns",
    [
        (dict(loads=True, loaded=["gmail"]), dict(loads=True, loaded=["gmail"]), True),
        (dict(loads=True, loaded=["gmail"]), dict(loads=False), True),
        (dict(loads=True, loaded=[]), dict(loads=True, loaded=[]), False),
        (dict(loads=False), dict(loads=False), False),  # the harness comparison
    ],
)
def test_a_cross_backend_diff_warns_once_when_a_setup_may_confound_it(a, b, warns):
    a_run, b_run = _saved(**a), _saved(**b)
    b_run.run.backend = "codex"
    comp = diff_runs(a_run, b_run)
    assert comp.cross_backend_user_customizations is warns
    lines = [w for w in comp.warnings if "customizations" in w]
    assert len(lines) == (1 if warns else 0)
    if warns:
        assert "re-run both with --no-user-customizations" in lines[0]


@pytest.mark.parametrize(
    "full, cut, label",
    [
        (
            dict(loads=True, loaded=["gmail"]),
            dict(loads=True, loaded=["gmail"]),
            "without subject",
        ),
        (dict(loads=True, loaded=["gmail"]), dict(loads=False), None),
        (
            dict(loads=True, loaded=["gmail"]),
            dict(loads=True, loaded=["gmail", "drive"]),
            None,
        ),
    ],
)
def test_an_ablation_pair_needs_the_same_customizations(full, cut, label):
    comp = diff_runs(
        _saved(**full), _saved(**cut, skills=("keeper",), ablated=("subject",))
    )
    assert comp.b_label == label


@pytest.mark.parametrize(
    "loaded, label",
    [(["gmail"], "without subject"), (None, "without subject"), ([], "bare agent")],
)
def test_a_bare_agent_is_claimed_only_when_nothing_was_loaded(loaded, label):
    full = _saved(loads=True, loaded=loaded, skills=("subject",))
    cut = _saved(loads=True, loaded=loaded, skills=(), ablated=("subject",))
    assert diff_runs(full, cut).b_label == label


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


@pytest.mark.parametrize(
    "loads, loaded, shown",
    [
        (True, ["drive", "gmail"], "drive, gmail"),
        (True, None, "not listed by this backend"),
        (True, [], "none found"),
        (False, None, None),
    ],
)
def test_the_report_header_says_what_was_loaded(loads, loaded, shown):
    out = _render(_saved(loads=loads, loaded=loaded))
    if shown:
        assert "user customizations:" in out and shown in out
    else:
        assert "user customizations" not in out


def test_kind_prefixed_inventory_survives_saved_run_and_report():
    names = [
        "mcp:gmail",
        "plugin:review@market",
        "rules:CLAUDE.md",
        "settings:settings.json",
        "skill:personal",
    ]
    saved = _saved(loads=True, loaded=names)
    reloaded = RunResults.model_validate_json(saved.model_dump_json())
    assert reloaded.run.loaded_user_customizations == names
    rendered = _render(reloaded)
    assert all(name in rendered for name in names)

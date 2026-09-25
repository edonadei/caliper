"""The run environment: what every attempt of a run is given, resolved once.

See docs/CONTEXT.md → Run environment.
"""

from __future__ import annotations

import pytest

from caliper.environment import choose_user_customizations, resolve_environment
from caliper.harness.base import (
    HarnessConfigurationError,
)
from caliper.schema.spec import EvalSpec, McpServer, TaskSpec
from caliper.workdir import AttemptWorkdir

from conftest import ScriptedHarness


def _skill(tmp_path, name: str) -> str:
    skill = tmp_path / name
    skill.mkdir()
    (skill / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---\n\nbody\n")
    return f"./{name}/SKILL.md"


def _spec(tmp_path, **fields):
    spec_path = tmp_path / "s.eval.yaml"
    spec_path.write_text("tasks: []\n")
    fields.setdefault(
        "tasks", [TaskSpec(id="task-001", name="t", prompt="p", expect="x")]
    )
    return EvalSpec(**fields), spec_path


def _resolve(spec, spec_path, **overrides):
    kwargs = dict(
        harness=ScriptedHarness(supports_mcp=True),
        ablate=[],
        user_customizations=None,
        timeout=30,
    )
    kwargs.update(overrides)
    return resolve_environment(spec, spec_path, **kwargs)


def _context(environment, tmp_path, attempt: int = 1):
    task = TaskSpec(id="task-001", name="t", prompt="do it", expect="x")
    with AttemptWorkdir(tmp_path) as workdir:
        return environment.context(task, attempt, workdir), workdir


# --- user customizations --------------------------------------------------


@pytest.mark.parametrize(
    ("flag", "spec_setting", "load", "source"),
    [
        (None, None, True, "default"),
        (None, False, False, "spec"),
        (True, False, True, "flag"),
        (False, True, False, "flag"),
    ],
)
def test_the_invocation_then_the_spec_then_the_default(
    tmp_path, flag, spec_setting, load, source
):
    spec, _ = _spec(tmp_path, user_customizations=spec_setting)

    choice = choose_user_customizations(flag, spec, ScriptedHarness(supports_mcp=True))

    assert (choice.load, choice.source, choice.ignored) == (load, source, False)


def test_a_backend_without_mcp_loads_nothing_and_says_it_ignored_the_request(
    tmp_path,
):
    spec, _ = _spec(tmp_path)

    choice = choose_user_customizations(True, spec, ScriptedHarness())

    assert (choice.load, choice.source, choice.ignored) == (False, "flag", True)


def test_an_ignored_explicit_request_is_warned_about_once(tmp_path):
    spec, spec_path = _spec(tmp_path)
    warnings: list[str] = []

    environment = _resolve(
        spec,
        spec_path,
        harness=ScriptedHarness(),
        user_customizations=True,
        on_warning=warnings.append,
    )

    assert environment.user_customizations is False
    assert len(warnings) == 1 and "no effect" in warnings[0]


def test_an_ignored_default_is_not_warned_about(tmp_path):
    spec, spec_path = _spec(tmp_path)
    warnings: list[str] = []

    _resolve(
        spec,
        spec_path,
        harness=ScriptedHarness(),
        on_warning=warnings.append,
    )

    assert warnings == []


# --- MCP servers ------------------------------------------------------------


def test_no_mcp_block_hands_the_backend_none(tmp_path):
    spec, spec_path = _spec(tmp_path)

    ctx, _ = _context(_resolve(spec, spec_path), tmp_path)

    assert ctx.mcp_servers is None
    assert ctx.spec_mcp_names == frozenset()


def test_an_empty_mcp_block_still_isolates(tmp_path):
    spec, spec_path = _spec(tmp_path, mcp={})

    ctx, _ = _context(_resolve(spec, spec_path), tmp_path)

    assert ctx.mcp_servers == {}


def test_an_ablated_server_is_removed_but_stays_the_specs_name(tmp_path):
    servers = {
        "kept": McpServer(command="k"),
        "cut": McpServer(command="c"),
    }
    spec, spec_path = _spec(tmp_path, mcp=servers)

    environment = _resolve(spec, spec_path, ablate=["cut"])
    ctx, _ = _context(environment, tmp_path)

    assert set(ctx.mcp_servers) == {"kept"}
    assert ctx.spec_mcp_names == frozenset({"kept", "cut"})
    assert environment.ablated == ["mcp:cut"]


def test_declared_servers_on_a_backend_without_mcp_are_refused(tmp_path):
    spec, spec_path = _spec(tmp_path, mcp={"s": McpServer(command="s")})

    with pytest.raises(HarnessConfigurationError, match="does not support MCP"):
        _resolve(spec, spec_path, harness=ScriptedHarness())


# --- skills and the per-attempt context -------------------------------------


def test_an_ablated_skill_is_not_installed_but_stays_the_specs_name(tmp_path):
    spec, spec_path = _spec(
        tmp_path, skills=[_skill(tmp_path, "a"), _skill(tmp_path, "b")]
    )

    environment = _resolve(spec, spec_path, ablate=["b"])
    ctx, _ = _context(environment, tmp_path)

    assert [ref.name for ref in ctx.skill_refs] == ["a"]
    assert ctx.spec_skill_names == frozenset({"a", "b"})


def test_an_ablated_skill_drops_every_activation_expectation(tmp_path):
    task = TaskSpec(id="task-001", name="t", prompt="p", expect="x", activates=["a"])
    spec, spec_path = _spec(
        tmp_path, skills=[_skill(tmp_path, "a"), _skill(tmp_path, "b")], tasks=[task]
    )

    assert _resolve(spec, spec_path).expected_activation(task) == ["a"]
    ablated = _resolve(spec, spec_path, ablate=["b"])
    assert ablated.expected_activation(task) is None


def test_the_context_carries_the_attempt_and_the_run_settings(tmp_path):
    spec, spec_path = _spec(tmp_path)

    ctx, workdir = _context(_resolve(spec, spec_path, timeout=45), tmp_path, attempt=2)

    assert (ctx.task_id, ctx.attempt, ctx.prompt) == ("task-001", 2, "do it")
    assert ctx.timeout == 45
    assert ctx.model is None
    assert (ctx.workdir, ctx.isolated_home) == (workdir.path, workdir.home)
    assert ctx.user_customizations is True

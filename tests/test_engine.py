"""Which engine runs a skill and which grades it (docs/CONTEXT.md → Engine as
runtime axis, docs/adr/0034)."""

from __future__ import annotations

import pytest

from caliper.commands.diagnosis import CannotRun
from caliper.commands.engine import Engine, check_judge_cli, resolve_engine
from caliper.schema.spec import EvalSpec, load_spec


@pytest.mark.parametrize(
    ("model", "judge_model", "expected"),
    [
        (None, None, Engine("claude-code", None, "claude-code", None, False)),
        # The backend is followed, the skill's model is not.
        (
            "codex:gpt-5-codex",
            None,
            Engine("codex", "gpt-5-codex", "codex", None, False),
        ),
        ("codex", "pi:m", Engine("codex", None, "pi", "m", True)),
        # A bare --judge-model reads like a bare --model: a claude-code model.
        ("codex", "opus", Engine("codex", None, "claude-code", "opus", True)),
        ("opus", None, Engine("claude-code", "opus", "claude-code", None, False)),
        ("claude", None, Engine("claude-code", None, "claude-code", None, False)),
    ],
)
def test_the_judge_follows_the_skill_backend_unless_named(
    model, judge_model, expected
) -> None:
    assert resolve_engine(model, judge_model) == expected


@pytest.mark.parametrize(
    ("model", "judge_model", "flag"),
    [("foo:bar", None, "--model"), (None, "bogus:x", "--judge-model")],
)
def test_an_unknown_backend_names_the_flag_and_the_known_backends(
    model, judge_model, flag
) -> None:
    with pytest.raises(CannotRun) as raised:
        resolve_engine(model, judge_model)

    message = str(raised.value)
    assert f"in {flag}" in message
    assert "Known backends: claude-code, codex, hermes, pi." in message


@pytest.fixture
def no_pi(monkeypatch, tmp_path):
    """No pi CLI anywhere caliper looks: no override, nothing on PATH."""
    monkeypatch.delenv("PI_CLI_PATH", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))


def _spec(tmp_path, check: str) -> EvalSpec:
    path = tmp_path / "s.eval.yaml"
    path.write_text(f"tasks:\n  - {{name: t, prompt: p, {check}}}\n")
    return load_spec(path)


_EXPECT = "expect: it worked"
_ASSERT = "assert: 'assert True'"


def test_a_missing_named_judge_points_back_to_the_model_backend(
    tmp_path, no_pi
) -> None:
    with pytest.raises(CannotRun) as raised:
        check_judge_cli(resolve_engine("codex", "pi"), _spec(tmp_path, _EXPECT))

    assert str(raised.value) == (
        "--judge-model pi asks pi to grade the `expect:` checks, but the pi CLI "
        "isn't installed.\n\n"
        "Install and sign in to the pi CLI, or remove --judge-model and codex "
        "(your --model) will grade too."
    )


def test_a_missing_named_judge_on_the_model_backend_offers_another_backend(
    tmp_path, no_pi
) -> None:
    # --judge-model named the same missing CLI as --model: neither removing the
    # flag nor changing --model alone would help.
    with pytest.raises(CannotRun) as raised:
        check_judge_cli(resolve_engine("pi", "pi"), _spec(tmp_path, _EXPECT))

    assert str(raised.value) == (
        "--judge-model pi asks pi to grade the `expect:` checks, but the pi CLI "
        "isn't installed.\n\n"
        "Install and sign in to the pi CLI, or point --judge-model at an "
        "installed backend."
    )


def test_a_missing_default_judge_points_at_the_model_flag(tmp_path, no_pi) -> None:
    with pytest.raises(CannotRun) as raised:
        check_judge_cli(resolve_engine("pi", None), _spec(tmp_path, _EXPECT))

    assert str(raised.value) == (
        "The pi CLI isn't installed. It would run the agent (--model) and, "
        "with no --judge-model, grade the `expect:` checks too.\n\n"
        "Install and sign in to the pi CLI, or pick an installed one with "
        "--model."
    )


def test_a_spec_without_expect_never_needs_the_judge_cli(tmp_path, no_pi) -> None:
    assert (
        check_judge_cli(resolve_engine("codex", "pi"), _spec(tmp_path, _ASSERT)) is None
    )


def test_an_installed_judge_cli_passes(monkeypatch, tmp_path, no_pi) -> None:
    pi = tmp_path / "pi"
    pi.write_text("")
    monkeypatch.setenv("PI_CLI_PATH", str(pi))

    assert (
        check_judge_cli(resolve_engine("codex", "pi"), _spec(tmp_path, _EXPECT)) is None
    )


def test_the_engine_builds_the_backends_it_names() -> None:
    engine = resolve_engine("codex:gpt-5-codex", "pi:m")

    assert (engine.harness().name, engine.harness().model) == ("codex", "gpt-5-codex")
    assert (engine.judge().backend, engine.judge().model) == ("pi", "m")

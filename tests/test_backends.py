"""The backend registry: one table every surface reads its backends from."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from caliper.backends import BACKENDS, normalize_backend
from caliper.harness import get_harness
from caliper.main import app
from caliper.schema.spec import parse_target

runner = CliRunner()


@pytest.mark.parametrize("name", sorted(BACKENDS))
def test_every_registered_backend_builds_a_harness_of_that_name(name) -> None:
    harness = get_harness(name, "some-model")

    assert (harness.name, harness.model) == (name, "some-model")


@pytest.mark.parametrize("alias", ["claude", "claude_code"])
def test_an_alias_means_the_same_backend_on_every_surface(alias) -> None:
    assert normalize_backend(alias) == "claude-code"
    assert parse_target(alias) == ("claude-code", None)
    assert parse_target(f"{alias}:m") == ("claude-code", "m")
    assert get_harness(alias).name == "claude-code"


def test_an_unknown_backend_is_refused_by_name() -> None:
    with pytest.raises(ValueError, match="Unknown backend: 'bogus'"):
        get_harness("bogus")


def test_caliper_does_not_manage_agent_cli_installs() -> None:
    """Each CLI updates itself; a run records which build it used instead."""
    result = runner.invoke(app, ["update-cli", "--check"])

    assert result.exit_code == 2
    assert "No such command" in result.output

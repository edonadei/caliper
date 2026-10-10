"""The backend registry: one table every surface reads its backends from."""

from __future__ import annotations

import subprocess

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


def test_update_cli_points_hermes_at_its_own_updater() -> None:
    result = runner.invoke(app, ["update-cli", "hermes", "--yes"])

    assert result.exit_code == 1
    assert "hermes update" in result.output


def test_update_cli_all_updates_every_npm_cli_around_a_self_updating_one(
    monkeypatch, tmp_path
) -> None:
    installs = []

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["npm", "install", "-g"]:
            installs.append(cmd[3])
        return subprocess.CompletedProcess(cmd, 0, stdout="1.0.0\n", stderr="")

    monkeypatch.setattr("caliper.commands.update_cli.shutil.which", lambda n: n)
    monkeypatch.setattr("caliper.harness.codex.CODEX_APP_CLI", tmp_path / "none")
    for var in ("CODEX_CLI_PATH", "HERMES_CLI_PATH", "PI_CLI_PATH"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("caliper.commands.update_cli.subprocess.run", fake_run)

    result = runner.invoke(app, ["update-cli", "all", "--yes"])

    assert result.exit_code == 0, result.output
    assert installs == [
        "@anthropic-ai/claude-code",
        "@openai/codex",
        "@earendil-works/pi-coding-agent",
    ]
    assert "hermes update" in result.output

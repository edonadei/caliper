from __future__ import annotations

import subprocess

from typer.testing import CliRunner

from caliper.main import app


runner = CliRunner()


def test_update_cli_check_prints_versions(monkeypatch, tmp_path) -> None:
    codex = tmp_path / "codex"
    codex.write_text("")

    def fake_which(name: str) -> str | None:
        if name == "npm":
            return "npm"
        if name == "codex":
            return str(codex)
        return None

    def fake_run(cmd, **kwargs):
        if cmd == [str(codex), "--version"]:
            return subprocess.CompletedProcess(
                cmd, 0, stdout="codex-cli 0.132.0\n", stderr=""
            )
        if cmd == ["npm", "view", "@openai/codex", "version"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="0.133.0\n", stderr="")
        if cmd == ["npm", "view", "@anthropic-ai/claude-code", "version"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="1.2.3\n", stderr="")
        raise AssertionError(cmd)

    monkeypatch.setattr("caliper.commands.update_cli.shutil.which", fake_which)
    monkeypatch.setattr(
        "caliper.commands.update_cli.CODEX_APP_CLI", tmp_path / "missing-codex"
    )
    monkeypatch.setattr("caliper.commands.update_cli.subprocess.run", fake_run)

    result = runner.invoke(app, ["update-cli", "codex", "--check"])

    assert result.exit_code == 0, result.output
    assert "codex-cli 0.132.0" in result.output
    assert "0.133.0" in result.output


def test_update_cli_requires_target_for_updates() -> None:
    result = runner.invoke(app, ["update-cli"])

    assert result.exit_code == 1
    assert "choose a CLI to update" in result.output


def test_update_cli_updates_with_npm_when_confirmed(monkeypatch, tmp_path) -> None:
    codex = tmp_path / "codex"
    codex.write_text("")
    calls = []

    def fake_which(name: str) -> str | None:
        if name == "npm":
            return "npm"
        if name == "codex":
            return str(codex)
        return None

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd == ["npm", "install", "-g", "@openai/codex"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd == [str(codex), "--version"]:
            return subprocess.CompletedProcess(
                cmd, 0, stdout="codex-cli 0.133.0\n", stderr=""
            )
        raise AssertionError(cmd)

    monkeypatch.setattr("caliper.commands.update_cli.shutil.which", fake_which)
    monkeypatch.setattr(
        "caliper.commands.update_cli.CODEX_APP_CLI", tmp_path / "missing-codex"
    )
    monkeypatch.setattr("caliper.commands.update_cli.subprocess.run", fake_run)

    result = runner.invoke(app, ["update-cli", "codex", "--yes"])

    assert result.exit_code == 0, result.output
    assert ["npm", "install", "-g", "@openai/codex"] in calls
    assert "Updated codex" in result.output


def test_update_cli_refuses_codex_app_bundle(monkeypatch, tmp_path) -> None:
    app_codex = tmp_path / "Codex.app" / "Contents" / "Resources" / "codex"
    app_codex.parent.mkdir(parents=True)
    app_codex.write_text("")

    monkeypatch.setattr("caliper.commands.update_cli.CODEX_APP_CLI", app_codex)

    result = runner.invoke(app, ["update-cli", "codex", "--yes"])

    assert result.exit_code == 1
    assert "Codex app bundle detected" in result.output

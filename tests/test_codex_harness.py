from __future__ import annotations

import subprocess

from verdict.harness.codex import CodexHarness


def test_codex_cli_receives_injected_skill_on_stdin(monkeypatch, tmp_path) -> None:
    skill = tmp_path / "SKILL.md"
    skill.write_text("---\ndescription: test\n---\n\nUse verdict carefully.")
    calls = []

    def fake_which(name: str) -> str:
        assert name == "codex"
        return "codex.cmd"

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd == ["codex.cmd", "--version"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="codex-cli 0.132.0\n", stderr="")
        assert cmd[:2] == ["codex.cmd", "exec"]
        assert cmd[-1] == "-"
        assert kwargs["input"].startswith("[Skill context]\nUse verdict carefully.")
        assert kwargs["input"].endswith("\n\nValidate the spec")
        assert kwargs["cwd"] == str(tmp_path)
        return subprocess.CompletedProcess(cmd, 0, stdout="VALID\n", stderr="")

    monkeypatch.setattr("verdict.harness.codex.shutil.which", fake_which)
    monkeypatch.setattr("verdict.harness.codex.subprocess.run", fake_run)

    result = CodexHarness().run(
        task_id="task-001",
        attempt=1,
        prompt="Validate the spec",
        skill_path=str(skill),
        model="gpt-5.4-mini",
        timeout=30,
        isolated_home=str(tmp_path),
        extra_path=[str(tmp_path / "bin")],
    )

    assert result.exit_code == 0
    assert result.final_output == "VALID"
    exec_cmd = calls[1][0]
    assert "--model" in exec_cmd
    assert exec_cmd[exec_cmd.index("--model") + 1] == "gpt-5.4-mini"
    assert "--skip-git-repo-check" in exec_cmd
    assert "--dangerously-bypass-approvals-and-sandbox" in exec_cmd


def test_codex_uses_api_when_cli_is_not_runnable(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("verdict.harness.codex.shutil.which", lambda _name: "codex.exe")

    def fake_run(cmd, **kwargs):
        raise OSError("access denied")

    api_calls = []

    def fake_api(self, prompt: str, model: str, timeout: int):
        api_calls.append((prompt, model, timeout))
        return "api ok", 0, None

    monkeypatch.setattr("verdict.harness.codex.subprocess.run", fake_run)
    monkeypatch.setattr(CodexHarness, "_run_api", fake_api)

    result = CodexHarness(model="fallback-model").run(
        task_id="task-001",
        attempt=1,
        prompt="Hello",
        skill_path=None,
        model=None,
        timeout=12,
        isolated_home=str(tmp_path),
    )

    assert result.exit_code == 0
    assert result.final_output == "api ok"
    assert api_calls == [("Hello", "fallback-model", 12)]

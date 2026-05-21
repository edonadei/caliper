from __future__ import annotations

import json
import subprocess

from verdict.harness.claude_code import ClaudeCodeHarness


def test_claude_harness_accepts_runner_contract_with_extra_path(monkeypatch, tmp_path) -> None:
    skill = tmp_path / "review.md"
    skill.write_text("Review the code.")
    run_calls = []

    def fake_run(cmd, **kwargs):
        run_calls.append((cmd, kwargs))
        stdout = "\n".join(
            [
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {"content": [{"type": "text", "text": "done"}]},
                    }
                ),
                json.dumps({"type": "result", "result": "done"}),
            ]
        )
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr("verdict.harness.claude_code.subprocess.run", fake_run)

    result = ClaudeCodeHarness(model="claude-test").run(
        task_id="task-001",
        attempt=1,
        prompt="/review the diff",
        skill_path=str(skill),
        model=None,
        timeout=30,
        isolated_home=str(tmp_path / "home"),
        extra_path=[str(tmp_path / "bin")],
    )

    assert result.exit_code == 0
    assert result.final_output == "done"

    cmd, kwargs = run_calls[0]
    assert cmd[:2] == ["claude", "-p"]
    assert cmd[2] == "/review the diff"
    assert "--dangerously-skip-permissions" in cmd
    assert cmd[cmd.index("--model") + 1] == "claude-test"
    assert kwargs["env"]["PATH"].startswith(str(tmp_path / "bin"))
    assert not list((tmp_path / "home" / ".claude" / "commands").glob("*.md"))

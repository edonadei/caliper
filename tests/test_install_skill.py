from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from caliper.commands.install_skill import load_packaged_skill
from caliper.main import app


runner = CliRunner()


def test_load_packaged_skill_resource() -> None:
    skill_text = load_packaged_skill()
    repo_skill = Path("skills/evaluate-skill/SKILL.md").read_text(encoding="utf-8")

    assert "# Evaluate Skill" in skill_text
    assert "caliper" in skill_text
    assert skill_text == repo_skill


def test_install_skill_codex_writes_expected_path(tmp_path) -> None:
    result = runner.invoke(app, ["install-skill", "codex"], env={"HOME": str(tmp_path)})

    destination = tmp_path / ".codex" / "skills" / "evaluate-skill" / "SKILL.md"
    assert result.exit_code == 0, result.output
    assert destination.exists()
    assert destination.read_text(encoding="utf-8") == load_packaged_skill()


def test_install_skill_claude_code_writes_expected_path(tmp_path) -> None:
    result = runner.invoke(app, ["install-skill", "claude-code"], env={"HOME": str(tmp_path)})

    destination = tmp_path / ".claude" / "commands" / "evaluate-skill.md"
    assert result.exit_code == 0, result.output
    assert destination.exists()
    assert destination.read_text(encoding="utf-8") == load_packaged_skill()


def test_install_skill_refuses_to_overwrite_without_force(tmp_path) -> None:
    destination = tmp_path / ".codex" / "skills" / "evaluate-skill" / "SKILL.md"
    destination.parent.mkdir(parents=True)
    destination.write_text("existing", encoding="utf-8")

    result = runner.invoke(app, ["install-skill", "codex"], env={"HOME": str(tmp_path)})

    assert result.exit_code != 0
    assert "already exists" in result.output
    assert destination.read_text(encoding="utf-8") == "existing"


def test_install_skill_force_overwrites_existing_file(tmp_path) -> None:
    destination = tmp_path / ".codex" / "skills" / "evaluate-skill" / "SKILL.md"
    destination.parent.mkdir(parents=True)
    destination.write_text("existing", encoding="utf-8")

    result = runner.invoke(
        app,
        ["install-skill", "codex", "--force"],
        env={"HOME": str(tmp_path)},
    )

    assert result.exit_code == 0, result.output
    assert destination.read_text(encoding="utf-8") == load_packaged_skill()


def test_install_skill_dry_run_does_not_write(tmp_path) -> None:
    result = runner.invoke(
        app,
        ["install-skill", "codex", "--dry-run"],
        env={"HOME": str(tmp_path)},
    )

    destination = tmp_path / ".codex" / "skills" / "evaluate-skill" / "SKILL.md"
    assert result.exit_code == 0, result.output
    assert "Would install evaluate-skill" in result.output
    assert not destination.exists()

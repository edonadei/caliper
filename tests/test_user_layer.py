"""User customizations observed at the CLI attempt boundary."""

import json
import subprocess
from pathlib import Path

import pytest

from caliper.harness.claude_code import ClaudeCodeHarness
from caliper.harness.codex import CodexHarness
from caliper.harness.hermes import HermesHarness
from caliper.skills import SkillRef
from conftest import patch_cli_calls, run_context


@pytest.mark.parametrize(
    "backend,folder",
    [
        (ClaudeCodeHarness, ".claude"),
        (CodexHarness, ".codex"),
        (HermesHarness, ".hermes"),
    ],
)
@pytest.mark.parametrize("loads", [True, False])
def test_user_skills_are_copied_and_declared_names_win(
    monkeypatch, tmp_path, backend, folder, loads
):
    home = tmp_path / "real"
    for name in ["personal", "subject", "ablated"]:
        skill = home / folder / "skills" / name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: test\n---\nuser"
        )
    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_text("---\nname: subject\ndescription: test\n---\nspec")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("caliper.harness.base.shutil.which", lambda n: n)
    monkeypatch.setattr("caliper.harness.codex.CODEX_APP_CLI", tmp_path / "missing")
    monkeypatch.setattr(
        ClaudeCodeHarness, "_seed_credentials_from_keychain", lambda *a: None
    )

    def fake(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout=json.dumps({"type": "system", "subtype": "init", "mcp_servers": []}),
            stderr="",
        )

    patch_cli_calls(monkeypatch, fake)
    iso = tmp_path / "iso"
    result = backend().run(
        run_context(
            isolated_home=str(iso),
            user_customizations=loads,
            skill_refs=[SkillRef("subject", source / "SKILL.md")],
            spec_skill_names=frozenset({"subject", "ablated"}),
        )
    )
    root = iso / folder / "skills"
    assert (root / "subject" / "SKILL.md").read_text().endswith("spec")
    assert not (root / "ablated").exists()
    assert (root / "personal" / "SKILL.md").exists() is loads
    if loads:
        assert result.loaded_user_customizations == ["skill:personal"]
        (root / "personal" / "SKILL.md").write_text("attempt mutation")
        assert (
            (home / folder / "skills" / "personal" / "SKILL.md")
            .read_text()
            .endswith("user")
        )
    else:
        assert result.loaded_user_customizations is None


@pytest.mark.parametrize(
    "backend,folder,settings,rules,content,marker",
    [
        (
            ClaudeCodeHarness,
            ".claude",
            "settings.json",
            "CLAUDE.md",
            '{"hooks": {}, "env": {"PERSONAL": "yes"}}',
            "PERSONAL",
        ),
        (
            CodexHarness,
            ".codex",
            "config.toml",
            "AGENTS.md",
            'developer_instructions = "personal"\nmodel_reasoning_effort = "high"\n',
            "developer_instructions",
        ),
        (
            HermesHarness,
            ".hermes",
            "config.yaml",
            None,
            "model:\n  provider: anthropic\nagent:\n  max_turns: 9\n",
            "max_turns",
        ),
    ],
)
@pytest.mark.parametrize("loads", [True, False])
def test_settings_and_rules_follow_the_switch(
    monkeypatch, tmp_path, backend, folder, settings, rules, content, marker, loads
):
    home = tmp_path / "real"
    real = home / folder
    real.mkdir(parents=True)
    (real / settings).write_text(content)
    if rules:
        (real / rules).write_text("Personal instructions")
    (real / "SOUL.md").write_text("Persona")
    (real / "MEMORY.md").write_text("Mutable memory")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("caliper.harness.base.shutil.which", lambda n: n)
    monkeypatch.setattr("caliper.harness.codex.CODEX_APP_CLI", tmp_path / "missing")
    monkeypatch.setattr(
        ClaudeCodeHarness, "_seed_credentials_from_keychain", lambda *a: None
    )
    patch_cli_calls(
        monkeypatch,
        lambda cmd, **kwargs: subprocess.CompletedProcess(
            cmd,
            0,
            stdout=json.dumps({"type": "system", "subtype": "init", "mcp_servers": []}),
            stderr="",
        ),
    )
    iso = tmp_path / "iso"
    result = backend().run(
        run_context(isolated_home=str(iso), user_customizations=loads)
    )
    staged = iso / folder
    text = (staged / settings).read_text() if (staged / settings).exists() else ""
    assert (marker in text) is loads
    if rules:
        assert (staged / rules).exists() is loads
    assert not (staged / "SOUL.md").exists()
    assert not (staged / "MEMORY.md").exists()
    if loads:
        expected = [f"settings:{settings}"]
        if rules:
            expected.append(f"rules:{rules}")
        assert result.loaded_user_customizations == sorted(expected)


@pytest.mark.parametrize("loads", [True, False])
@pytest.mark.parametrize(
    "skill_location,skill_name,command_name",
    [
        ("skills/review", "review", "review:review"),
        ("skills/review", "fancy", "review:fancy"),
        (".", "root-skill", "review:root-skill"),
        ("skills/review", "review:fancy", "review:fancy"),
    ],
)
def test_claude_plugins_use_private_installations(
    monkeypatch, tmp_path, loads, skill_location, skill_name, command_name
):
    home = tmp_path / "real"
    real = home / ".claude"
    plugins = real / "plugins"
    plugin = plugins / "cache" / "market" / "review" / "1.0"
    (plugin / "examples").mkdir(parents=True)
    (plugin / "examples/SKILL.md").write_text("Example, not an installed skill")
    (plugin / skill_location).mkdir(parents=True, exist_ok=True)
    (plugin / skill_location / "SKILL.md").write_text(
        f"---\nname: {skill_name}\ndescription: Review\n---\nReview code"
    )
    (real / "settings.json").write_text(
        json.dumps(
            {"enabledPlugins": {"review@market": True, "disabled@market": False}}
        )
    )
    (plugins / "installed_plugins.json").write_text(
        json.dumps(
            {
                "version": 2,
                "plugins": {
                    "review@market": [
                        {"scope": "user", "installPath": str(plugin), "version": "1.0"}
                    ],
                    "project@market": [{"scope": "project", "installPath": "/missing"}],
                },
            }
        )
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(
        ClaudeCodeHarness, "_seed_credentials_from_keychain", lambda *a: None
    )
    patch_cli_calls(
        monkeypatch,
        lambda cmd, **kwargs: subprocess.CompletedProcess(
            cmd,
            0,
            stdout=json.dumps({"type": "system", "subtype": "init", "mcp_servers": []}),
            stderr="",
        ),
    )
    iso = tmp_path / "iso"
    result = ClaudeCodeHarness().run(
        run_context(isolated_home=str(iso), user_customizations=loads)
    )
    registry = iso / ".claude/plugins/installed_plugins.json"
    assert registry.exists() is loads
    if loads:
        entries = json.loads(registry.read_text())["plugins"]
        assert set(entries) == {"review@market"}
        installed = Path(entries["review@market"][0]["installPath"])
        assert installed.is_relative_to(iso)
        assert (installed / skill_location / "SKILL.md").is_file()
        assert result.loaded_user_customizations == [
            "plugin:review@market",
            "settings:settings.json",
            f"skill:{command_name}",
        ]
        assert command_name in result.user_skill_names


@pytest.mark.parametrize(
    "frontmatter", ["description: Review", "name: renamed\ndescription: Review"]
)
def test_claude_user_skills_keep_the_directory_command_name(
    monkeypatch, tmp_path, frontmatter
):
    home = tmp_path / "real"
    source = home / ".claude/skills/native"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(f"---\n{frontmatter}\n---\nReview")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(
        ClaudeCodeHarness, "_seed_credentials_from_keychain", lambda *a: None
    )
    patch_cli_calls(
        monkeypatch,
        lambda cmd, **kwargs: subprocess.CompletedProcess(
            cmd,
            0,
            stdout=json.dumps({"type": "system", "subtype": "init", "mcp_servers": []}),
            stderr="",
        ),
    )
    iso = tmp_path / "iso"
    result = ClaudeCodeHarness().run(
        run_context(isolated_home=str(iso), user_customizations=True)
    )
    assert (iso / ".claude/skills/native/SKILL.md").is_file()
    assert result.loaded_user_customizations == ["skill:native"]

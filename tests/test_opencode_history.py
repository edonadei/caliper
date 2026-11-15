"""Planted OpenCode history uses the invocation's real model selection."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from conftest import run_context

from caliper.harness.base import HarnessConfigurationError
from caliper.harness.opencode import OpenCodeHarness
from caliper.schema.spec import PlantedTurn


@pytest.fixture
def history_cli(monkeypatch, tmp_path):
    """An actual subprocess that models native resume's inherited model choice."""
    marker = tmp_path / "imported"
    (tmp_path / "import").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('imported')\n"
    )
    (tmp_path / "run").write_text(
        "import json, sys\n"
        "if '--model' in sys.argv:\n"
        " model = sys.argv[sys.argv.index('--model') + 1]\n"
        " print(json.dumps({'type': 'text', 'part': {'text': model}}))\n"
        "else:\n"
        " print('Model not found: caliper/planted.', file=sys.stderr)\n"
        " sys.exit(1)\n"
    )
    monkeypatch.setattr(OpenCodeHarness, "cli_path", lambda _self: sys.executable)
    return marker


@pytest.mark.parametrize(
    ("engine", "context", "expected"),
    [
        (None, None, "local/default"),
        ("local/engine", None, "local/engine"),
        ("local/engine", "local/context", "local/context"),
    ],
)
def test_history_resumes_on_the_explicit_or_configured_model(
    tmp_path, history_cli, engine, context, expected
) -> None:
    config = Path.home() / ".config" / "opencode" / "opencode.json"
    config.parent.mkdir(parents=True)
    config.write_text('{"model": "local/default"}')
    ctx = run_context(
        isolated_home=str(tmp_path / "home"),
        workdir=str(tmp_path),
        model=context,
        history=[PlantedTurn(user="Earlier question")],
    )

    result = OpenCodeHarness(model=engine).run(ctx)

    assert result.final_output == expected
    assert history_cli.read_text() == "imported"


def test_history_without_a_selected_model_is_refused_before_import(
    tmp_path, history_cli
) -> None:
    ctx = run_context(
        isolated_home=str(tmp_path / "home"),
        workdir=str(tmp_path),
        history=[PlantedTurn(user="Earlier question")],
    )

    with pytest.raises(HarnessConfigurationError, match="history.*requires a model"):
        OpenCodeHarness().run(ctx)

    assert not history_cli.exists()


@pytest.mark.parametrize("source", ["config_agent", "markdown_agent"])
def test_history_defers_to_an_agent_level_model_under_user_customizations(
    monkeypatch, tmp_path, source
) -> None:
    """Agent-level models beat top-level ``model`` natively, so ``--model`` is omitted."""
    marker = tmp_path / "imported"
    (tmp_path / "import").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('imported')\n"
    )
    (tmp_path / "run").write_text(
        "import json, sys\n"
        "flag = sys.argv[sys.argv.index('--model') + 1] if '--model' in sys.argv else 'native-agent'\n"
        "print(json.dumps({'type': 'text', 'part': {'text': flag}}))\n"
    )
    monkeypatch.setattr(OpenCodeHarness, "cli_path", lambda _self: sys.executable)

    opencode_home = Path.home() / ".config" / "opencode"
    opencode_home.mkdir(parents=True)
    if source == "config_agent":
        (opencode_home / "opencode.json").write_text(
            '{"model": "local/top", "agent": {"build": {"model": "local/agent"}}}'
        )
    else:
        (opencode_home / "agents").mkdir()
        (opencode_home / "agents" / "build.md").write_text(
            "---\nmodel: local/md-agent\n---\nBuild carefully."
        )

    ctx = run_context(
        isolated_home=str(tmp_path / "home"),
        workdir=str(tmp_path),
        user_customizations=True,
        history=[PlantedTurn(user="Earlier question")],
    )

    result = OpenCodeHarness().run(ctx)

    assert result.final_output == "native-agent"
    assert marker.read_text() == "imported"


def test_nested_subagent_build_md_does_not_mask_missing_primary_model(
    tmp_path, history_cli
) -> None:
    """``agents/sub/build.md`` defines ``sub/build``, not the primary ``build`` agent."""
    nested = Path.home() / ".config" / "opencode" / "agents" / "sub"
    nested.mkdir(parents=True)
    (nested / "build.md").write_text("---\nmodel: local/sub\n---\nSubagent.")
    ctx = run_context(
        isolated_home=str(tmp_path / "home"),
        workdir=str(tmp_path),
        user_customizations=True,
        history=[PlantedTurn(user="Earlier question")],
    )

    with pytest.raises(HarnessConfigurationError, match="history.*requires a model"):
        OpenCodeHarness().run(ctx)


def test_markdown_frontmatter_name_can_select_the_primary_agent_model(
    monkeypatch, tmp_path
) -> None:
    (tmp_path / "import").write_text("")
    (tmp_path / "run").write_text(
        "import json, sys\n"
        "flag = sys.argv[sys.argv.index('--model') + 1] if '--model' in sys.argv else 'frontmatter-named'\n"
        "print(json.dumps({'type': 'text', 'part': {'text': flag}}))\n"
    )
    monkeypatch.setattr(OpenCodeHarness, "cli_path", lambda _self: sys.executable)

    agents = Path.home() / ".config" / "opencode" / "agents"
    agents.mkdir(parents=True)
    (agents / "primary.md").write_text(
        "---\nname: build\nmodel: local/named\n---\nPrimary agent."
    )
    ctx = run_context(
        isolated_home=str(tmp_path / "home"),
        workdir=str(tmp_path),
        user_customizations=True,
        history=[PlantedTurn(user="Earlier question")],
    )

    result = OpenCodeHarness().run(ctx)

    assert result.final_output == "frontmatter-named"

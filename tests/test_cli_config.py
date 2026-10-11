"""The CLI config file each backend rewrites, tested through one interface."""

from __future__ import annotations

import stat

import pytest

from caliper.harness.base import HarnessConfigurationError
from caliper.harness.codex import CODEX_CONFIG
from caliper.harness.hermes import HERMES_CONFIG
from caliper.schema.spec import McpServer
from tests.conftest import run_context

# Each backend's config, with a connection key it keeps and an ambient key it
# drops when isolated.
CONFIGS = [
    pytest.param(
        CODEX_CONFIG, "codex", "model_provider", "approval_policy", id="codex"
    ),
    pytest.param(HERMES_CONFIG, "hermes", "provider", "personality", id="hermes"),
]


def _user_config(home, config, connection, ambient):
    path = home / config.path
    path.parent.mkdir(parents=True)
    path.write_text(
        config.codec.dumps(
            {
                connection: "kept",
                ambient: "leaked",
                config.servers_key: {"personal": {"command": "mine"}},
            }
        )
    )


def _write(config, backend, tmp_path, **ctx):
    iso = tmp_path / "iso"
    context = run_context(isolated_home=str(iso), **ctx)
    config.write(context, backend=backend)
    return context


@pytest.mark.parametrize(("config", "backend", "connection", "ambient"), CONFIGS)
def test_isolated_keeps_only_connection_keys_and_declared_servers(
    monkeypatch, tmp_path, config, backend, connection, ambient
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    _user_config(home, config, connection, ambient)

    ctx = _write(
        config, backend, tmp_path, mcp_servers={"echo": McpServer(command="echo")}
    )

    written = config.read(ctx)
    assert written[connection] == "kept"
    assert ambient not in written
    assert config.servers(ctx) == {"echo"}
    mode = (tmp_path / "iso" / config.path).stat().st_mode
    assert stat.S_IMODE(mode) == 0o600


@pytest.mark.parametrize(("config", "backend", "connection", "ambient"), CONFIGS)
def test_user_customizations_merge_servers_and_the_spec_wins(
    monkeypatch, tmp_path, config, backend, connection, ambient
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    _user_config(home, config, connection, ambient)

    ctx = _write(
        config,
        backend,
        tmp_path,
        mcp_servers={"personal": McpServer(command="declared")},
        spec_mcp_names=frozenset({"personal"}),
        user_customizations=True,
    )

    written = config.read(ctx)
    assert written[ambient] == "leaked"
    assert written[config.servers_key]["personal"]["command"] == "declared"


@pytest.mark.parametrize(("config", "backend", "connection", "ambient"), CONFIGS)
def test_nothing_is_written_without_a_config_or_a_server(
    monkeypatch, tmp_path, config, backend, connection, ambient
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    ctx = _write(config, backend, tmp_path, mcp_servers=None)

    assert not (tmp_path / "iso" / config.path).exists()
    assert config.servers(ctx) == set()


@pytest.mark.parametrize(("config", "backend", "connection", "ambient"), CONFIGS)
def test_a_config_the_cli_cannot_parse_is_refused(
    monkeypatch, tmp_path, config, backend, connection, ambient
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    path = home / config.path
    path.parent.mkdir(parents=True)
    path.write_text("[unclosed\n  - : {")

    with pytest.raises(
        HarnessConfigurationError, match=f"not valid {config.codec.name}"
    ):
        _write(config, backend, tmp_path)

"""The CLI config file each backend rewrites, tested through one interface."""

from __future__ import annotations

import stat
import sys

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
    """Write the attempt's copy from the operator's at ``home``; its config home."""
    attempt = tmp_path / "iso" / "config-home"
    context = run_context(isolated_home=str(tmp_path / "iso"), **ctx)
    real = tmp_path / "home" / config.path
    config.write(context, attempt, backend=backend, real=real)
    return attempt


@pytest.mark.parametrize(("config", "backend", "connection", "ambient"), CONFIGS)
def test_isolated_keeps_only_connection_keys_and_declared_servers(
    tmp_path, config, backend, connection, ambient
) -> None:
    _user_config(tmp_path / "home", config, connection, ambient)

    attempt = _write(
        config, backend, tmp_path, mcp_servers={"echo": McpServer(command="echo")}
    )

    written = config.read(attempt)
    assert written[connection] == "kept"
    assert ambient not in written
    assert config.servers(attempt) == {"echo"}
    mode = (attempt / config.path).stat().st_mode
    assert stat.S_IMODE(mode) == 0o600


@pytest.mark.parametrize(("config", "backend", "connection", "ambient"), CONFIGS)
def test_user_customizations_merge_servers_and_the_spec_wins(
    tmp_path, config, backend, connection, ambient
) -> None:
    _user_config(tmp_path / "home", config, connection, ambient)

    attempt = _write(
        config,
        backend,
        tmp_path,
        mcp_servers={"personal": McpServer(command="declared")},
        spec_mcp_names=frozenset({"personal"}),
        user_customizations=True,
    )

    written = config.read(attempt)
    assert written[ambient] == "leaked"
    assert written[config.servers_key]["personal"]["command"] == "declared"


@pytest.mark.parametrize(("config", "backend", "connection", "ambient"), CONFIGS)
def test_nothing_is_written_without_a_config_or_a_server(
    monkeypatch, tmp_path, config, backend, connection, ambient
) -> None:
    # Pinned settings are written regardless: hermes pins some on Windows.
    monkeypatch.setattr(sys, "platform", "linux")

    attempt = _write(config, backend, tmp_path, mcp_servers=None)

    assert not (attempt / config.path).exists()
    assert config.servers(attempt) == set()


@pytest.mark.parametrize(("config", "backend", "connection", "ambient"), CONFIGS)
def test_a_config_the_cli_cannot_parse_is_refused(
    tmp_path, config, backend, connection, ambient
) -> None:
    path = tmp_path / "home" / config.path
    path.parent.mkdir(parents=True)
    path.write_text("[unclosed\n  - : {")

    with pytest.raises(
        HarnessConfigurationError, match=f"not valid {config.codec.name}"
    ):
        _write(config, backend, tmp_path)


@pytest.mark.parametrize(("config", "backend", "connection", "ambient"), CONFIGS)
def test_a_utf8_config_survives_a_non_utf8_locale(
    tmp_path, config, backend, connection, ambient
) -> None:
    # Windows' default text codec is cp1252, which cannot decode most UTF-8.
    real = tmp_path / "real.cfg"
    real.write_bytes(config.codec.dumps({connection: "café → ☕"}).encode("utf-8"))
    attempt = tmp_path / "iso" / "config-home"

    config.write(
        run_context(isolated_home=str(tmp_path / "iso")),
        attempt,
        backend=backend,
        real=real,
    )

    assert config.read(attempt)[connection] == "café → ☕"

"""Hermes on Windows (#302): where the attempt's home lives, and what it is given.

On Windows hermes boots only from a ``HERMES_HOME`` under the root that owns its
installed dependencies, so the attempt home is placed there instead of in the
temp dir (docs/adr/0037-hermes-attempt-home-lives-under-the-hermes-root-on-windows.md).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml
from conftest import patch_cli_calls, run_context

from caliper.harness.hermes import HERMES_CONFIG, HermesHarness, user_hermes_home

windows_only = pytest.mark.skipif(
    sys.platform != "win32", reason="directory junctions are Windows-only"
)


def _version(cmd):
    return subprocess.CompletedProcess(
        cmd, 0, stdout="Hermes Agent v0.22.5\n", stderr=""
    )


def _user_root(tmp_path, monkeypatch) -> Path:
    """A fake ``%LOCALAPPDATA%\\hermes`` holding auth, config and a persona."""
    local = tmp_path / "local"
    root = local / "hermes"
    root.mkdir(parents=True)
    (root / "auth.json").write_text("{}")
    (root / ".env").write_text("SECRET=1\n")
    (root / "config.yaml").write_text("model:\n  provider: anthropic\n")
    (root / "SOUL.md").write_text("You are a quirky persona.")
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    return root


@pytest.mark.parametrize(
    ("platform", "expected"),
    [
        ("win32", ["local", "hermes"]),
        ("linux", ["home", ".hermes"]),
        ("darwin", ["home", ".hermes"]),
    ],
)
def test_user_hermes_home_is_the_platform_default(
    platform, expected, monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

    assert user_hermes_home() == tmp_path.joinpath(*expected)


def test_windows_without_localappdata_falls_back_under_the_profile(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

    assert user_hermes_home() == tmp_path / "home" / "AppData" / "Local" / "hermes"


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_shell_is_found_on_path_only_on_windows(platform, monkeypatch) -> None:
    # Windows' CreateProcess cannot resolve the POSIX path /bin/sh (WinError 2).
    monkeypatch.setattr(sys, "platform", platform)
    argv, _, _ = HermesHarness()._command(run_context())

    assert argv[0] == ("sh" if platform == "win32" else "/bin/sh")


def test_windows_attempt_env_names_a_home_python_can_resolve(
    monkeypatch, tmp_path
) -> None:
    # Python on Windows ignores HOME: Path.home() reads USERPROFILE, and hermes
    # finds its own root through LOCALAPPDATA.
    monkeypatch.setattr(sys, "platform", "win32")
    # shutil.which takes its Windows branch on the faked platform, which needs
    # _winapi; the lookup is beside the point here.
    monkeypatch.setattr(HermesHarness, "cli_path", lambda _self: "hermes")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\me\AppData\Local")
    monkeypatch.setenv("USERPROFILE", r"C:\Users\me")
    ctx = run_context(isolated_home=str(tmp_path))

    env = HermesHarness()._environment(ctx)

    assert env["USERPROFILE"] == str(tmp_path)
    assert env["LOCALAPPDATA"] == r"C:\Users\me\AppData\Local"


def test_posix_attempt_env_is_unchanged(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("LOCALAPPDATA", "/nowhere")
    ctx = run_context(isolated_home=str(tmp_path))

    env = HermesHarness()._environment(ctx)

    assert "USERPROFILE" not in env
    assert "LOCALAPPDATA" not in env
    assert env["HERMES_HOME"] == str(tmp_path / ".hermes")


def test_windows_user_layer_reads_the_real_hermes_root(monkeypatch, tmp_path) -> None:
    root = _user_root(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "platform", "win32")
    harness = HermesHarness()
    ctx = run_context(isolated_home=str(tmp_path / "iso"))

    isolated = harness.skills_root(ctx) / "my-skill"

    assert harness.user_path(isolated, ctx) == root / "skills" / "my-skill"


@pytest.mark.parametrize("loads", [True, False])
def test_windows_config_pins_a_standalone_gateway(monkeypatch, tmp_path, loads) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    real = tmp_path / "real.yaml"
    real.write_text("model: m\ngateway:\n  standalone: false\n  port: 1\n")
    ctx = run_context(isolated_home=str(tmp_path / "iso"), user_customizations=loads)
    home = tmp_path / "iso" / ".hermes"

    HERMES_CONFIG.write(ctx, home, backend="hermes", real=real)

    gateway = HERMES_CONFIG.read(home)["gateway"]
    assert gateway["standalone"] is True
    # The user's other gateway settings load only with their customizations.
    assert ("port" in gateway) is loads


def test_windows_config_is_written_even_when_the_user_has_none(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    ctx = run_context(isolated_home=str(tmp_path / "iso"))
    home = tmp_path / "iso" / ".hermes"

    HERMES_CONFIG.write(ctx, home, backend="hermes", real=tmp_path / "missing.yaml")

    assert HERMES_CONFIG.read(home) == {"gateway": {"standalone": True}}


def test_posix_config_pins_nothing(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    ctx = run_context(isolated_home=str(tmp_path / "iso"))
    home = tmp_path / "iso" / ".hermes"

    HERMES_CONFIG.write(ctx, home, backend="hermes", real=tmp_path / "missing.yaml")

    assert HERMES_CONFIG.read(home) == {}


@windows_only
def test_windows_attempt_home_lives_under_the_hermes_root(
    monkeypatch, tmp_path
) -> None:
    root = _user_root(tmp_path, monkeypatch)
    iso = tmp_path / "iso"
    iso.mkdir()
    seen = {}

    def fake_run(cmd, **kwargs):
        if cmd[1:] == ["--version"]:
            return _version(cmd)
        home = Path(kwargs["env"]["HERMES_HOME"])
        seen["home"] = home
        seen["files"] = sorted(p.name for p in home.iterdir())
        seen["config"] = yaml.safe_load((home / "config.yaml").read_text())
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("caliper.harness.base.shutil.which", lambda _n: "hermes")
    patch_cli_calls(monkeypatch, fake_run)

    HermesHarness().run(run_context(isolated_home=str(iso)))

    home = seen["home"]
    # A named profile under the root, so hermes shares its committed
    # dependencies...
    assert home.parent == root / "profiles"
    assert re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", home.name)
    # ...that opts out of multiplexing, so its gateway serves only the attempt
    # and the user's running gateway never adopts it.
    assert seen["config"]["gateway"] == {"standalone": True}
    assert seen["config"]["model"] == {"provider": "anthropic"}
    # Seeded from the real Windows root; persona still stripped (docs/adr/0005).
    assert {"auth.json", ".env", "config.yaml"} <= set(seen["files"])
    assert "SOUL.md" not in seen["files"]
    # Released once the attempt is read: neither the home nor the link remains.
    assert not home.exists()
    assert not os.path.lexists(iso / ".hermes")


@windows_only
def test_windows_attempt_home_is_released_when_the_attempt_raises(
    monkeypatch, tmp_path
) -> None:
    root = _user_root(tmp_path, monkeypatch)
    iso = tmp_path / "iso"
    iso.mkdir()

    def fake_run(cmd, **kwargs):
        if cmd[1:] == ["--version"]:
            return _version(cmd)
        raise RuntimeError("spawn failed")

    monkeypatch.setattr("caliper.harness.base.shutil.which", lambda _n: "hermes")
    patch_cli_calls(monkeypatch, fake_run)

    with pytest.raises(RuntimeError, match="spawn failed"):
        HermesHarness().run(run_context(isolated_home=str(iso)))

    assert list((root / "profiles").iterdir()) == []
    assert not os.path.lexists(iso / ".hermes")


@windows_only
def test_sweep_removes_only_stale_caliper_attempts(monkeypatch, tmp_path) -> None:
    root = _user_root(tmp_path, monkeypatch)
    profiles = root / "profiles"
    stale, fresh, users = (
        profiles / "caliper-stale",
        profiles / "caliper-fresh",
        profiles / "caliper-mine",
    )
    for home in (stale, fresh, users):
        home.mkdir(parents=True)
    # The user's own profile happens to share the prefix but carries no marker.
    for ours in (stale, fresh):
        (ours / ".caliper-attempt").write_text("")
    day_old = time.time() - 2 * 24 * 60 * 60
    for home in (stale, users):
        os.utime(home, (day_old, day_old))
    monkeypatch.setattr("caliper.harness.base.shutil.which", lambda _n: "hermes")
    patch_cli_calls(
        monkeypatch,
        lambda cmd, **_: (
            _version(cmd)
            if cmd[1:] == ["--version"]
            else subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        ),
    )
    iso = tmp_path / "iso"
    iso.mkdir()

    HermesHarness().run(run_context(isolated_home=str(iso)))

    assert sorted(p.name for p in profiles.iterdir()) == [
        "caliper-fresh",
        "caliper-mine",
    ]


@windows_only
def test_windows_release_stops_the_attempts_own_gateway(monkeypatch, tmp_path) -> None:
    _user_root(tmp_path, monkeypatch)
    iso = tmp_path / "iso"
    iso.mkdir()
    calls = []

    def fake_run(cmd, **kwargs):
        if cmd[1:] == ["--version"]:
            return _version(cmd)
        home = Path(kwargs["env"]["HERMES_HOME"])
        calls.append((cmd, home))
        if cmd[0] == "sh":
            # hermes -z leaves an --idle-exit gateway behind for the profile.
            (home / "gateway.lock").write_text("{}")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("caliper.harness.base.shutil.which", lambda _n: "hermes")
    patch_cli_calls(monkeypatch, fake_run)

    HermesHarness().run(run_context(isolated_home=str(iso)))

    (_, attempt_home), (stop, stopped_home) = calls
    assert stop == ["hermes", "gateway", "stop"]
    assert stopped_home == attempt_home
    assert not attempt_home.exists()


@windows_only
def test_a_held_file_keeps_the_marker_for_a_later_sweep(tmp_path) -> None:
    from caliper.harness.hermes import _remove_attempt_home

    home = tmp_path / "caliper-held"
    (home / "logs").mkdir(parents=True)
    (home / ".caliper-attempt").write_text("")
    (home / "auth.json").write_text("{}")
    held = home / "logs" / "gateway.log"

    with held.open("w"):
        _remove_attempt_home(home)
        # Everything free went; the marker stayed beside the held file.
        assert sorted(p.name for p in home.iterdir()) == [".caliper-attempt", "logs"]

    _remove_attempt_home(home)
    assert not home.exists()

"""What every CLI-agent backend does the same way.

These are the chores :class:`CliHarness` owns — seeding the isolated home,
locating the CLI, building the environment, and reading the stream's tail. Each
claim is made once here and parameterized over the four backends, so a fifth
inherits the suite by existing rather than by copying assertions.

The adapters' own test files still carry claims of their own that overlap these
(hermes asserts its neutral seeding to pin docs/adr/0005, for one) — those are
kept deliberately: they test the *backend's* stance, not the shared chore, and
would need writing again the day the shared implementation grew an exception.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from caliper.harness.base import CliHarness, ConversationTurn
from caliper.harness.claude_code import ClaudeCodeHarness
from caliper.harness.codex import CodexHarness
from caliper.harness.hermes import HermesHarness
from caliper.harness.pi import PiHarness

from conftest import run_context

ALL_BACKENDS = [ClaudeCodeHarness, CodexHarness, HermesHarness, PiHarness]

# The three backends that locate their binary through ``cli_path``. claude-code
# is absent on purpose: it spawns a bare ``claude`` and leaves the lookup to the
# shell, so it declares no ``cli_name``.
DISCOVERING_BACKENDS = [CodexHarness, HermesHarness, PiHarness]


def _prepared(harness: CliHarness, tmp_path: Path):
    """Run the home-preparation half of the template and return the context."""
    ctx = run_context(isolated_home=str(tmp_path / "home"))
    Path(ctx.isolated_home).mkdir(parents=True, exist_ok=True)
    harness._seed_home(ctx)
    harness._prepare(ctx)
    return ctx


@pytest.fixture(autouse=True)
def _no_keychain(monkeypatch):
    """No backend shells out to the macOS Keychain during these tests."""
    monkeypatch.setattr(CliHarness, "_capture_output", lambda *a, **k: None)


# --- seeding the isolated home ----------------------------------------------


@pytest.mark.parametrize("backend", ALL_BACKENDS)
def test_seeds_declared_files_verbatim(backend, monkeypatch, tmp_path) -> None:
    """Every declared seed file that exists is copied byte-for-byte (ADR-0012)."""
    real_home = tmp_path / "real"
    monkeypatch.setattr(Path, "home", lambda: real_home)

    harness = backend()
    ctx = run_context(isolated_home=str(tmp_path / "home"))
    for src, _dst in harness.seed_files(ctx):
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text(f"contents of {src.name}")

    harness._seed_home(ctx)

    seeds = harness.seed_files(ctx)
    assert seeds, f"{harness.name} declares no seed files"
    for src, dst in seeds:
        assert dst.read_text() == f"contents of {src.name}"


@pytest.mark.parametrize("backend", ALL_BACKENDS)
def test_seeding_skips_files_the_user_does_not_have(
    backend, monkeypatch, tmp_path
) -> None:
    """A missing real config is not an error — the CLI falls back to its own."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "empty")

    harness = backend()
    ctx = run_context(isolated_home=str(tmp_path / "home"))
    harness._seed_home(ctx)

    assert not any(dst.exists() for _src, dst in harness.seed_files(ctx))


# --- locating the CLI --------------------------------------------------------


@pytest.mark.parametrize("backend", DISCOVERING_BACKENDS)
def test_cli_path_env_var_overrides_discovery(backend, monkeypatch, tmp_path) -> None:
    harness = backend()
    override = tmp_path / "custom-cli"
    override.write_text("")
    monkeypatch.setenv(harness.cli_path_env_var, str(override))
    monkeypatch.setattr("caliper.harness.base.shutil.which", lambda _n: "/usr/bin/x")

    assert harness.cli_path() == str(override)


@pytest.mark.parametrize("backend", DISCOVERING_BACKENDS)
def test_stale_cli_path_falls_through_to_discovery(
    backend, monkeypatch, tmp_path
) -> None:
    """A pointer at a binary that is gone must not fail the run by itself.

    The override names a path, not an intent to fail: an export left over from a
    reinstall should let discovery proceed rather than produce "CLI not found"
    while the CLI sits on PATH.
    """
    harness = backend()
    monkeypatch.setenv(harness.cli_path_env_var, str(tmp_path / "deleted"))
    monkeypatch.setattr(backend, "cli_candidates", lambda _self: ())
    monkeypatch.setattr("caliper.harness.base.shutil.which", lambda _n: "/usr/bin/x")

    assert harness.cli_path() == "/usr/bin/x"


@pytest.mark.parametrize("backend", DISCOVERING_BACKENDS)
def test_cli_path_is_none_when_nothing_is_installed(backend, monkeypatch) -> None:
    harness = backend()
    monkeypatch.delenv(harness.cli_path_env_var, raising=False)
    monkeypatch.setattr(backend, "cli_candidates", lambda _self: ())
    monkeypatch.setattr("caliper.harness.base.shutil.which", lambda _n: None)

    assert harness.cli_path() is None


# --- the attempt's environment -----------------------------------------------


@pytest.mark.parametrize("backend", ALL_BACKENDS)
def test_home_points_at_the_isolated_home(backend, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "real")
    harness = backend()
    ctx = _prepared(harness, tmp_path)

    assert harness._environment(ctx)["HOME"] == ctx.isolated_home


@pytest.mark.parametrize("backend", ALL_BACKENDS)
def test_extra_path_wins_over_the_real_path(backend, monkeypatch, tmp_path) -> None:
    """The run's own staged binaries come first, and only once.

    ``extra_path`` is how a run puts a shim ahead of the real tool; an entry that
    is already on PATH has to move to the front rather than merely appear twice,
    or the shim never runs.
    """
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "real")
    monkeypatch.setenv("PATH", f"/usr/bin{os.pathsep}/staged{os.pathsep}/bin")

    harness = backend()
    ctx = _prepared(harness, tmp_path)
    ctx.extra_path = ["/staged"]

    parts = harness._environment(ctx)["PATH"].split(os.pathsep)
    assert parts[0] == "/staged"
    assert parts.count("/staged") == 1


@pytest.mark.parametrize("backend", ALL_BACKENDS)
def test_locale_and_scratch_space_are_forwarded(backend, monkeypatch, tmp_path) -> None:
    """Locale and TMPDIR ride along; nothing else is inherited by default."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "real")
    monkeypatch.setenv("LANG", "en_GB.UTF-8")
    monkeypatch.setenv("TMPDIR", "/scratch")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "leaked")

    harness = backend()
    env = harness._environment(_prepared(harness, tmp_path))

    assert env["LANG"] == "en_GB.UTF-8"
    assert env["TMPDIR"] == "/scratch"
    assert "AWS_SECRET_ACCESS_KEY" not in env


@pytest.mark.parametrize("backend", ALL_BACKENDS)
def test_skills_root_lives_inside_the_isolated_home(
    backend, monkeypatch, tmp_path
) -> None:
    """Install-and-discover stages the neighbourhood where only this attempt sees it."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "real")
    harness = backend()
    ctx = _prepared(harness, tmp_path)

    assert harness.skills_root(ctx).is_relative_to(Path(ctx.isolated_home))


# --- reading the stream ------------------------------------------------------


class _StubHarness(CliHarness):
    """A backend that parses nothing, so the base's own tail is what is measured."""

    def __init__(self, transcript, final_output="") -> None:
        self._transcript = transcript
        self._final_output = final_output

    name = "stub"

    def skills_root(self, ctx):  # pragma: no cover - unused here
        raise NotImplementedError

    def _command(self, ctx):  # pragma: no cover - unused here
        raise NotImplementedError

    def _environment(self, ctx):  # pragma: no cover - unused here
        raise NotImplementedError

    def _parse_stream(self, stdout):
        return self._transcript, self._final_output


def test_stream_tail_recovers_the_last_assistant_turn() -> None:
    """A stream that ends on a tool call still has a final answer: the last thing said."""
    harness = _StubHarness(
        [
            ConversationTurn(role="assistant", content="first"),
            ConversationTurn(role="assistant", content="second"),
            ConversationTurn(role="tool_use", content="[tool: shell] ls"),
        ]
    )

    assert harness._parse_stream_with_tail("")[1] == "second"


def test_stream_tail_defers_to_an_explicit_final_answer() -> None:
    harness = _StubHarness(
        [ConversationTurn(role="assistant", content="chatter")],
        final_output="the answer",
    )

    assert harness._parse_stream_with_tail("")[1] == "the answer"


def test_stream_tail_ignores_empty_and_non_assistant_turns() -> None:
    harness = _StubHarness(
        [
            ConversationTurn(role="assistant", content="real"),
            ConversationTurn(role="assistant", content=""),
            ConversationTurn(role="user", content="a question"),
        ]
    )

    assert harness._parse_stream_with_tail("")[1] == "real"


def test_stream_tail_is_empty_when_the_agent_never_spoke() -> None:
    harness = _StubHarness([ConversationTurn(role="tool_use", content="[tool: shell]")])

    assert harness._parse_stream_with_tail("")[1] == ""

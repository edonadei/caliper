"""The seam every backend implements: one attempt in, one result out.

What the caller hands across it, what the backend may assume about it, and what
must not survive from one invocation to the next. The four adapters' own files
test what each *does* with an attempt; this file tests the handover itself.
"""

from __future__ import annotations

import json
import subprocess

from caliper.harness.base import RunContext
from caliper.harness.claude_code import ClaudeCodeHarness

from conftest import patch_cli_calls, run_context


def _agent_says(text: str):
    def fake_run(cmd, **kwargs):
        stdout = "\n".join(
            [
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {"content": [{"type": "text", "text": text}]},
                    }
                ),
                json.dumps({"type": "result", "result": text}),
            ]
        )
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    return fake_run


class _Recording(ClaudeCodeHarness):
    """A real backend that remembers the context each invocation was handed."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.seen: list[RunContext] = []

    def _command(self, ctx: RunContext):
        self.seen.append(ctx)
        return super()._command(ctx)


def _run(harness: ClaudeCodeHarness, tmp_path, **overrides):
    ctx = run_context(isolated_home=str(tmp_path / "home"), **overrides)
    harness.run(ctx)
    return ctx


def test_the_backends_own_model_answers_a_request_that_names_none(
    monkeypatch, tmp_path
) -> None:
    """``None`` means "whatever engine this backend was built with".

    The engine is resolved once at the run seam (docs/adr/0004), never per spec,
    so the caller says nothing and the backend supplies its own.
    """
    patch_cli_calls(monkeypatch, _agent_says("done"))
    harness = _Recording(model="backend-default")

    caller_ctx = _run(harness, tmp_path)

    assert harness.seen[0].model == "backend-default"
    # And the caller's own context is not edited on the way through: the
    # resolution is the backend's business, not a write-back.
    assert caller_ctx.model is None


def test_a_named_model_is_not_overridden_by_the_backends_own(
    monkeypatch, tmp_path
) -> None:
    patch_cli_calls(monkeypatch, _agent_says("done"))
    harness = _Recording(model="backend-default")

    _run(harness, tmp_path, model="asked-for")

    assert harness.seen[0].model == "asked-for"


def test_an_empty_model_falls_back_like_an_absent_one(monkeypatch, tmp_path) -> None:
    """`""` is not a model any more than `None` is, and never was."""
    patch_cli_calls(monkeypatch, _agent_says("done"))
    harness = _Recording(model="backend-default")

    _run(harness, tmp_path, model="")

    assert harness.seen[0].model == "backend-default"


def test_each_invocation_gets_its_own_context(monkeypatch, tmp_path) -> None:
    """A retry is a second invocation, and it is handed a second context.

    The harness object is shared across the runner's worker threads and reused
    across a retried attempt, so the context is the only per-attempt state a
    backend has (docs/adr/0019). Anything it needs between hooks is derived
    from the context, never stashed on it or on the instance — which is what
    lets the caller build a fresh one per shot and be sure nothing leaks across.
    """
    patch_cli_calls(monkeypatch, _agent_says("done"))
    harness = _Recording()

    _run(harness, tmp_path, attempt=1)
    _run(harness, tmp_path, attempt=2)

    assert harness.seen[0] is not harness.seen[1]
    assert [ctx.attempt for ctx in harness.seen] == [1, 2]


def test_the_context_owns_its_lists(tmp_path) -> None:
    """A backend cannot reach back through the seam and edit the caller's state.

    The runner holds one neighbourhood for the whole run and hands it to every
    attempt; the harness object is shared across worker threads. A backend that
    appended to what it was given would otherwise be editing the next attempt's
    inputs, on another thread.
    """
    neighbourhood = []
    forbidden = ["answer.md"]
    ctx = RunContext(
        task_id="task-001",
        attempt=1,
        prompt="hi",
        skill_refs=neighbourhood,
        model=None,
        timeout=30,
        isolated_home=str(tmp_path),
        workdir=str(tmp_path / "work"),
        extra_path=[],
        forbidden_files=forbidden,
    )

    ctx.skill_refs.append("intruder")
    ctx.forbidden_files.append("intruder")

    assert neighbourhood == []
    assert forbidden == ["answer.md"]

"""A judge's CLI refusal is read and acted on the way an attempt's is.

One classifier (``caliper.harness.refusal.classify``) over what the CLI wrote,
and one response per kind: a misconfiguration or a spending cap stops the run,
a throttle is retried (docs/adr/0019, docs/adr/0030).
"""

from __future__ import annotations

import json
import subprocess

import pytest
from conftest import patch_cli_calls

from caliper import cancel
from caliper.harness.base import (
    ConversationTurn,
    HarnessConfigurationError,
    LoginRequired,
)
from caliper.harness.claude_code import ClaudeCodeHarness
from caliper.harness.codex import CodexHarness
from caliper.harness.refusal import RefusalKind
from caliper.judge.eval_judge import EvalJudge
from caliper.retry import SpendingCapReached
from caliper.schema.spec import TaskSpec
from caliper.workdir import StepCancelled

# Recorded from `claude -p "say ok" --output-format json --model claude-sonnet-4-20250514`
# against a retired model (issue #75).
RETIRED_MODEL_ENVELOPE = {
    "type": "result",
    "subtype": "success",
    "is_error": True,
    "api_error_status": 404,
    "terminal_reason": "api_error",
    "result": (
        "There's an issue with the selected model (claude-sonnet-4-20250514). "
        "It may not exist or you may not have access to it."
    ),
    "modelUsage": {},
}


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    """A throttled judge is retried; the tests skip the wait between tries."""
    monkeypatch.setattr("caliper.retry.cancel.sleep_unless_stopped", lambda _s: False)


def _envelopes(monkeypatch, *replies: tuple[int, dict | str]) -> list[list[str]]:
    """Answer each claude call with the next ``(returncode, envelope)``."""
    calls: list[list[str]] = []
    queue = list(replies)

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        code, envelope = queue.pop(0) if len(queue) > 1 else queue[0]
        stdout = envelope if isinstance(envelope, str) else json.dumps(envelope)
        return subprocess.CompletedProcess(cmd, code, stdout=stdout, stderr="")

    patch_cli_calls(monkeypatch, fake_run)
    return calls


def _answer(text: str) -> dict:
    return {
        "type": "result",
        "is_error": False,
        "result": text,
        "modelUsage": {"claude-opus-4-8": {}},
    }


def test_claude_judge_reports_the_model_it_ran(monkeypatch) -> None:
    _envelopes(monkeypatch, (0, _answer("ok")))

    result = ClaudeCodeHarness().run_prompt("anything", cwd=".")

    assert result.text == "ok"
    assert result.resolved_model == "claude-opus-4-8"
    assert result.error is None
    assert result.refusal is None


def test_claude_judge_answer_without_an_envelope_is_still_the_answer(
    monkeypatch,
) -> None:
    # A CLI that stops wrapping its answer must not break the judge outright.
    _envelopes(monkeypatch, (0, "plain text"))

    result = ClaudeCodeHarness().run_prompt("anything", cwd=".")

    assert result.text == "plain text"
    assert result.error is None


def test_claude_judge_unclassified_is_error_passes_text_through(monkeypatch) -> None:
    envelope = {
        "type": "result",
        "is_error": True,
        "api_error_status": 500,
        "result": '{"mode": "verdict", "passed": false, "reasoning": "refused"}',
        "modelUsage": {},
    }
    _envelopes(monkeypatch, (0, envelope))

    result = ClaudeCodeHarness().run_prompt("anything", cwd=".")

    assert result.error is None
    assert result.refusal is None
    assert '"mode": "verdict"' in result.text


def test_claude_judge_on_an_unavailable_model_stops_the_run(monkeypatch) -> None:
    """Decided by the same diagnosis an attempt gets, worded for the judge."""
    _envelopes(monkeypatch, (1, RETIRED_MODEL_ENVELOPE))

    with pytest.raises(HarnessConfigurationError) as exc:
        ClaudeCodeHarness(model="claude-sonnet-4-20250514").run_prompt(
            "anything", cwd="."
        )

    # Logging in again cannot bring a retired model back.
    assert type(exc.value) is HarnessConfigurationError
    message = str(exc.value)
    assert message.startswith("The claude-code judge cannot run.")
    assert "claude-sonnet-4-20250514" in message
    assert "`--judge-model claude-code:<model>`" in message
    assert "`--model " not in message


# An expired OAuth login carries no status, only its words; a bare 401 the reverse.
@pytest.mark.parametrize(
    ("status", "said", "quoted"),
    [
        (
            None,
            "Failed to authenticate: OAuth session expired and could not be refreshed",
            "OAuth session expired and could not be refreshed",
        ),
        (401, "Request failed", "API error 401: Request failed"),
    ],
)
def test_claude_judge_with_a_lapsed_login_stops_the_run(
    monkeypatch, status, said, quoted
) -> None:
    envelope = {
        "type": "result",
        "is_error": True,
        "api_error_status": status,
        "result": said,
        "modelUsage": {},
    }
    _envelopes(monkeypatch, (1, envelope))
    monkeypatch.setattr(
        "caliper.harness.claude_code.shutil.which",
        lambda name, path=None: f"/opt/bin/{name}",
    )

    with pytest.raises(LoginRequired) as exc:
        ClaudeCodeHarness().run_prompt("anything", cwd=".")

    message = str(exc.value)
    assert message.startswith("The claude-code judge cannot run.")
    assert quoted in message
    assert "Run `/opt/bin/claude auth login`" in message
    assert exc.value.command == ["/opt/bin/claude", "auth", "login"]


def test_claude_judge_answer_about_a_login_failure_is_an_answer(monkeypatch) -> None:
    answer = '{"mode": "verdict", "passed": false, "reasoning": "401: not logged in"}'
    _envelopes(
        monkeypatch, (0, {"type": "result", "is_error": False, "result": answer})
    )

    result = ClaudeCodeHarness().run_prompt("anything", cwd=".")

    assert result.text == answer


def test_a_throttled_judge_is_retried_until_it_answers(monkeypatch) -> None:
    throttled = {**RETIRED_MODEL_ENVELOPE, "api_error_status": 429, "result": "slow"}
    calls = _envelopes(monkeypatch, (1, throttled), (0, _answer("ok")))

    result = ClaudeCodeHarness().run_prompt("anything", cwd=".")

    assert len(calls) == 2
    assert result.text == "ok"
    assert result.error is None
    assert result.refusal is None


def test_a_judge_still_throttled_after_its_retries_is_an_error(monkeypatch) -> None:
    throttled = {**RETIRED_MODEL_ENVELOPE, "api_error_status": 429, "result": "slow"}
    calls = _envelopes(monkeypatch, (1, throttled))

    result = ClaudeCodeHarness().run_prompt("anything", cwd=".")

    assert len(calls) == 3
    assert result.text == ""
    assert result.refusal is not None
    assert result.refusal.kind is RefusalKind.THROTTLE
    assert "rate limited" in (result.error or "")
    assert "API error 429: slow" in (result.error or "")


def test_a_judge_stopped_during_its_backoff_is_not_a_verdict(monkeypatch) -> None:
    # The run's cancellation ended the wait: no verdict, and no judge_error.
    throttled = {**RETIRED_MODEL_ENVELOPE, "api_error_status": 429, "result": "slow"}
    calls = _envelopes(monkeypatch, (1, throttled))

    def stopped(_seconds: float) -> bool:
        cancel.request()
        return True

    monkeypatch.setattr("caliper.retry.cancel.sleep_unless_stopped", stopped)

    with pytest.raises(StepCancelled):
        ClaudeCodeHarness().run_prompt("anything", cwd=".")

    assert len(calls) == 1


def test_a_judge_at_its_spending_cap_stops_the_run(monkeypatch) -> None:
    capped = {
        **RETIRED_MODEL_ENVELOPE,
        "api_error_status": None,
        "result": "You've hit your spending cap · resets 5pm",
    }
    calls = _envelopes(monkeypatch, (1, capped))

    with pytest.raises(SpendingCapReached) as exc:
        ClaudeCodeHarness().run_prompt("anything", cwd=".")

    assert len(calls) == 1
    assert "resets 5pm" in str(exc.value)


def test_a_codex_judge_at_its_spending_cap_stops_the_run(monkeypatch, tmp_path) -> None:
    # Every backend's judge gets the attempt's response, not just claude-code's.
    monkeypatch.setattr("caliper.harness.base.shutil.which", lambda _name: "codex")
    monkeypatch.setattr(
        "caliper.harness.codex.CODEX_APP_CLI", tmp_path / "missing-codex"
    )
    monkeypatch.delenv("CODEX_CLI_PATH", raising=False)

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd, 1, stdout="", stderr="ERROR: You've hit your usage limit.\n"
        )

    patch_cli_calls(monkeypatch, fake_run)

    with pytest.raises(SpendingCapReached):
        CodexHarness().run_prompt("anything", cwd=str(tmp_path))


def _task(**overrides) -> TaskSpec:
    fields = {"id": "t1", "name": "t", "prompt": "p", "expect": "says ok"}
    fields.update(overrides)
    return TaskSpec(**fields)


def test_eval_judge_stops_the_run_on_an_unavailable_model(
    monkeypatch, attempt_workdir
) -> None:
    """An unavailable judge model fails every attempt alike, so it is fatal.

    Recording it as a per-attempt ``judge_error`` would pay for every agent
    run only to discard it (issue #139).
    """
    _envelopes(monkeypatch, (1, RETIRED_MODEL_ENVELOPE))

    with pytest.raises(HarnessConfigurationError) as exc:
        EvalJudge(backend="claude-code", model="claude-sonnet-4-20250514").evaluate(
            task=_task(expect="x"),
            transcript=[ConversationTurn(role="assistant", content="hello")],
            workdir=attempt_workdir,
        )

    message = str(exc.value)
    assert "--judge-model" in message
    assert "claude-sonnet-4-20250514" in message
    assert "unparseable" not in message.lower()


def test_eval_judge_keeps_a_lasting_rate_limit_per_attempt(
    monkeypatch, attempt_workdir
) -> None:
    """A throttle can clear before the next attempt, so it stays a judge_error."""
    throttled = {**RETIRED_MODEL_ENVELOPE, "api_error_status": 429, "result": "slow"}
    _envelopes(monkeypatch, (1, throttled))

    result = EvalJudge(backend="claude-code").evaluate(
        task=_task(expect="x"),
        transcript=[ConversationTurn(role="assistant", content="hello")],
        workdir=attempt_workdir,
    )

    assert result.errored is True
    assert "rate limited" in (result.autorater_reasoning or "")

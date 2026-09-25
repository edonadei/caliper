from __future__ import annotations

from caliper.harness.refusal import (
    CliRefusal,
    ConfigSignal,
    RefusalKind,
    classify,
)


# --- what counts as a cap or a throttle --------------------------------------


def test_known_provider_signals_are_refusals() -> None:
    for text, kind in (
        ("Spending cap reached", RefusalKind.SPENDING_CAP),
        ("quota exceeded for this key", RefusalKind.SPENDING_CAP),
        ("rate limit exceeded", RefusalKind.THROTTLE),
        ("HTTP 429 Too Many Requests", RefusalKind.THROTTLE),
        ("the model is overloaded", RefusalKind.THROTTLE),
    ):
        refusal = classify(text, [])
        assert refusal is not None and refusal.kind is kind, text


# --- classify ---------------------------------------------------------------

LOGIN = ConfigSignal(("not logged in",), "Log in. The CLI said: {text}")


def test_nothing_the_cli_wrote_is_no_refusal() -> None:
    assert classify("", [LOGIN]) is None
    assert classify("   \n", [LOGIN]) is None
    assert classify("Reading prompt from stdin...", [LOGIN]) is None


def test_a_cap_wins_over_a_config_marker_it_brushes() -> None:
    refusal = classify(
        "You have reached your subscription usage limit. Not logged in? Try later.",
        [LOGIN, ConfigSignal(("subscription",), "subscription")],
    )
    assert refusal is not None
    assert refusal.kind is RefusalKind.SPENDING_CAP


def test_a_throttle_wins_over_a_config_marker_it_brushes() -> None:
    refusal = classify(
        "429 rate_limit_error: authentication rate limit exceeded",
        [ConfigSignal(("authentication",), "auth")],
    )
    assert refusal is not None
    assert refusal.kind is RefusalKind.THROTTLE


def test_a_config_marker_is_diagnosed_with_what_the_cli_said() -> None:
    refusal = classify("Error: Not logged in", [LOGIN])
    assert refusal == CliRefusal(
        RefusalKind.CONFIG, "Log in. The CLI said: Error: Not logged in"
    )


def test_a_structural_diagnosis_runs_after_cap_and_throttle() -> None:
    def diagnose(text: str) -> str | None:
        return "crashed" if "TypeError" in text else None

    assert classify("TypeError: x", [], diagnose) == CliRefusal(
        RefusalKind.CONFIG, "crashed"
    )
    capped = classify("TypeError: spending cap reached", [], diagnose)
    assert capped is not None and capped.kind is RefusalKind.SPENDING_CAP


def test_a_spending_cap_quotes_the_line_that_says_so() -> None:
    # codex opens its stream with its own chatter; the limit is further down.
    text = (
        '{"type":"thread.started","thread_id":"t"}\n'
        '{"type":"error","message":"You\'ve hit your usage limit. Upgrade to Pro '
        "(https://chatgpt.com/explore/pro), visit "
        "https://chatgpt.com/codex/settings/usage to purchase more credits or "
        'try again at Sep 24th, 2026 2:02 AM."}'
    )
    refusal = classify(text, [])
    assert refusal is not None
    assert refusal.message.endswith("try again at Sep 24th, 2026 2:02 AM.")
    assert "thread.started" not in refusal.message


def test_a_spending_cap_nested_in_a_json_event_is_quoted_by_its_text() -> None:
    # pi reports the limit inside the assistant message it failed to produce.
    text = (
        '{"type":"message_end","message":{"role":"assistant","content":[],'
        '"usage":{"input":0},"errorMessage":"Codex error: The usage limit '
        'has been reached"}}'
    )
    refusal = classify(text, [])
    assert refusal is not None
    assert refusal.message == "Codex error: The usage limit has been reached"

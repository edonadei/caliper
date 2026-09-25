from __future__ import annotations

from caliper.harness.refusal import looks_like_infra_failure


# --- looks_like_infra_failure --------------------------------------------


def test_looks_like_infra_matches_known_signals() -> None:
    for text in (
        "Spending cap reached",
        "rate limit exceeded",
        "HTTP 429 Too Many Requests",
        "the model is overloaded",
        "quota exceeded for this key",
    ):
        assert looks_like_infra_failure(text), text


def test_looks_like_infra_ignores_normal_output() -> None:
    assert not looks_like_infra_failure("The assistant wrote the file successfully.")
    assert not looks_like_infra_failure("")

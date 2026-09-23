from __future__ import annotations

import json

import pytest

from caliper.judge.jev import (
    API_KEY_ENV,
    JEV_ENDPOINT,
    JEV_MODEL,
    ChoiceQuestion,
    JevError,
    JevFailure,
    ask_choices,
)

KEY = "ts-secret-key-123"
ENV = {API_KEY_ENV: KEY}
QUESTION = {
    "grounded": ChoiceQuestion(
        instructions="Is it grounded?",
        criteria={"yes": "It is.", "no": "It is not.", "unsure": None},
    )
}


def answer(choice="yes", probs=None, model=JEV_MODEL) -> bytes:
    return json.dumps(
        {
            "model": model,
            "answers": {
                "grounded": {
                    "type": "choice",
                    "choice": choice,
                    "probabilities": probs or {"yes": 0.9, "no": 0.08, "unsure": 0.02},
                    "confidence": 0.85,
                }
            },
            "usage": {"input_tokens": 120, "output_tokens": 10},
        }
    ).encode()


def transport_returning(status: int, body: bytes, seen: list | None = None):
    def send(url, headers, payload, timeout):
        if seen is not None:
            seen.append((url, headers, json.loads(payload), timeout))
        return status, body

    return send


def test_sends_pinned_model_structured_state_and_bearer_key():
    seen: list = []
    response = ask_choices(
        {"task_prompt": "p", "tool_events": []},
        QUESTION,
        env=ENV,
        transport=transport_returning(200, answer(), seen),
    )
    url, headers, body, _ = seen[0]
    assert url == JEV_ENDPOINT
    assert headers["Authorization"] == f"Bearer {KEY}"
    assert body["model"] == JEV_MODEL
    assert body["state"] == {"task_prompt": "p", "tool_events": []}
    assert body["questions"]["grounded"]["type"] == "choice"
    assert body["questions"]["grounded"]["criteria"]["unsure"] is None

    got = response.answers["grounded"]
    assert got.choice == "yes"
    assert got.probability == pytest.approx(0.9)
    assert set(got.probabilities) == {"yes", "no", "unsure"}
    assert response.model == JEV_MODEL
    assert response.input_tokens == 120
    assert response.request_bytes > 0


def test_missing_key_is_an_auth_error_that_names_the_variable():
    with pytest.raises(JevError) as err:
        ask_choices("s", QUESTION, env={}, transport=transport_returning(200, b""))
    assert err.value.kind is JevFailure.AUTH
    assert API_KEY_ENV in err.value.message


@pytest.mark.parametrize(
    "status, kind",
    [
        (401, JevFailure.AUTH),
        (403, JevFailure.AUTH),
        (422, JevFailure.REJECTED),
        (429, JevFailure.PROVIDER),
        (500, JevFailure.PROVIDER),
        (529, JevFailure.PROVIDER),
    ],
)
def test_http_failures_are_typed_and_never_echo_the_key(status, kind):
    # A provider that echoes the key back must not get it into the message.
    body = json.dumps({"detail": f"bad token {KEY}"}).encode()
    with pytest.raises(JevError) as err:
        ask_choices("s", QUESTION, env=ENV, transport=transport_returning(status, body))
    assert err.value.kind is kind
    assert KEY not in err.value.message
    assert KEY not in str(err.value)


def test_unreachable_provider_is_a_provider_error():
    def send(*_):
        raise TimeoutError("timed out")

    with pytest.raises(JevError) as err:
        ask_choices("s", QUESTION, env=ENV, transport=send)
    assert err.value.kind is JevFailure.PROVIDER


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        json.dumps({"model": JEV_MODEL}).encode(),
        json.dumps({"answers": {}}).encode(),
        json.dumps({"model": JEV_MODEL, "answers": {}}).encode(),
        answer(choice="maybe"),
        answer(probs={"yes": 0.9, "no": 0.1}),
        answer(probs={"yes": "high", "no": 0.1, "unsure": 0.0}),
    ],
)
def test_malformed_success_bodies_are_malformed(body):
    with pytest.raises(JevError) as err:
        ask_choices("s", QUESTION, env=ENV, transport=transport_returning(200, body))
    assert err.value.kind is JevFailure.MALFORMED

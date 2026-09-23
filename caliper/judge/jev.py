"""A direct client for TypeSafe's Jev System One model.

Jev answers typed questions about a ``state`` with a calibrated probability
distribution instead of generated text. Caliper calls it over plain HTTP — no
SDK dependency — pinned to one concrete model version, so a tuned
``min_probability`` threshold cannot drift under an alias. See
docs/adr/0027-jev-classify-is-an-experimental-typed-check.md.

The key is read from ``TYPESAFE_API_KEY`` at call time and only ever placed in
the ``Authorization`` header. It is never logged, persisted, or echoed in an
error: every message built here is redacted before it leaves the module.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

JEV_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
# Pinned rather than ``jev-latest``: an alias moves when TypeSafe ships, and a
# threshold authored against one version's calibration would silently change
# meaning under the next.
JEV_MODEL = "jev-1.13.0"
API_KEY_ENV = "TYPESAFE_API_KEY"
DEFAULT_TIMEOUT_SECONDS = 30.0


class JevFailure(str, Enum):
    """Why a Jev call produced no usable answer. Every kind is a judge error."""

    # No key in the environment, or the provider rejected it (401/403).
    AUTH = "auth"
    # The provider refused the request body (422) — e.g. evidence over the
    # model's context budget.
    REJECTED = "rejected"
    # Throttled, overloaded, a 5xx, a timeout, or no connection at all.
    PROVIDER = "provider"
    # A 2xx whose body is not the documented answer shape.
    MALFORMED = "malformed"


class JevError(Exception):
    """A Jev call that yielded no verdict, with a secret-free explanation."""

    def __init__(self, kind: JevFailure, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


@dataclass(frozen=True)
class ChoiceQuestion:
    """One Choice question: pick exactly one of ``criteria``'s keys."""

    instructions: str | dict | list
    # Option name -> rubric description (``None`` when the name says it all).
    criteria: dict[str, str | dict | list | None]

    def to_json(self) -> dict:
        return {
            "type": "choice",
            "instructions": self.instructions,
            "criteria": self.criteria,
        }


@dataclass(frozen=True)
class ChoiceAnswer:
    choice: str
    # Every authored option mapped to its probability, kept whole so a saved
    # result shows the runner-up as well as the winner.
    probabilities: dict[str, float]
    confidence: float | None = None

    @property
    def probability(self) -> float:
        """The probability of the selected choice."""
        return self.probabilities.get(self.choice, 0.0)


@dataclass(frozen=True)
class JevResponse:
    # The concrete version that answered (e.g. ``jev-1.13.0``), as reported.
    model: str
    answers: dict[str, ChoiceAnswer]
    # Wall-clock seconds for the HTTP round trip.
    latency_seconds: float
    input_tokens: int | None = None
    # The serialized request size in bytes — recorded so an oversized-evidence
    # error can say how big the evidence was.
    request_bytes: int = 0
    raw: dict = field(default_factory=dict, repr=False)


# (url, headers, body, timeout) -> (status, body). Injected by tests so no
# socket is ever opened; the default speaks HTTP through urllib.
Transport = Callable[[str, dict[str, str], bytes, float], tuple[int, bytes]]


def _urllib_transport(
    url: str, headers: dict[str, str], body: bytes, timeout: float
) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read() or b""


def _redact(text: str, secret: str | None) -> str:
    if secret:
        text = text.replace(secret, "[redacted]")
    return text


def _provider_detail(body: bytes) -> str:
    """A short, human-readable excerpt of an error body."""
    text = body.decode("utf-8", errors="replace").strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text[:300]
    if isinstance(parsed, dict):
        for key in ("detail", "error", "message"):
            if key in parsed:
                return json.dumps(parsed[key], ensure_ascii=False)[:300]
    return text[:300]


def _parse_choice(qid: str, raw: object, options: set[str]) -> ChoiceAnswer:
    if not isinstance(raw, dict) or raw.get("type") != "choice":
        raise JevError(
            JevFailure.MALFORMED, f"answer '{qid}' is not a choice answer: {raw!r}"
        )
    choice = raw.get("choice")
    probabilities = raw.get("probabilities")
    if not isinstance(choice, str) or choice not in options:
        raise JevError(
            JevFailure.MALFORMED,
            f"answer '{qid}' selected {choice!r}, which is not an authored choice",
        )
    if not isinstance(probabilities, dict) or set(probabilities) != options:
        raise JevError(
            JevFailure.MALFORMED,
            f"answer '{qid}' did not return a probability for every choice",
        )
    try:
        probs = {name: float(p) for name, p in probabilities.items()}
    except (TypeError, ValueError):
        raise JevError(
            JevFailure.MALFORMED, f"answer '{qid}' has non-numeric probabilities"
        ) from None
    confidence = raw.get("confidence")
    return ChoiceAnswer(
        choice=choice,
        probabilities=probs,
        confidence=float(confidence) if isinstance(confidence, (int, float)) else None,
    )


def ask_choices(
    state: str | dict | list,
    questions: dict[str, ChoiceQuestion],
    *,
    model: str = JEV_MODEL,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    transport: Transport | None = None,
    env: dict[str, str] | None = None,
) -> JevResponse:
    """Ask Jev every question in one request and return the typed answers.

    Raises :class:`JevError` whenever no usable answer comes back. The caller
    decides what that means; for a classification check it is a judge error.
    """
    environ = os.environ if env is None else env
    key = (environ.get(API_KEY_ENV) or "").strip()
    if not key:
        raise JevError(
            JevFailure.AUTH,
            f"{API_KEY_ENV} is not set. Export a TypeSafe API key in the shell "
            "that runs caliper (create one in the TypeSafe console); caliper "
            "never reads it from a spec or a file.",
        )

    body = json.dumps(
        {
            "state": state,
            "model": model,
            "questions": {qid: q.to_json() for qid, q in questions.items()},
        },
        ensure_ascii=False,
    ).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "caliper-eval",
    }

    send = transport or _urllib_transport
    started = time.monotonic()
    try:
        status, payload = send(JEV_ENDPOINT, headers, body, timeout)
    except (TimeoutError, OSError) as err:
        raise JevError(
            JevFailure.PROVIDER,
            _redact(f"could not reach TypeSafe ({type(err).__name__}: {err})", key),
        ) from None
    latency = time.monotonic() - started

    if status in (401, 403):
        raise JevError(
            JevFailure.AUTH,
            f"TypeSafe rejected the API key in {API_KEY_ENV} (HTTP {status}). "
            "Check that it is current and has access to "
            f"{model}.",
        )
    if status == 422:
        raise JevError(
            JevFailure.REJECTED,
            _redact(
                f"TypeSafe rejected the request (HTTP 422, {len(body)} bytes "
                f"sent): {_provider_detail(payload)}",
                key,
            ),
        )
    if status != 200:
        raise JevError(
            JevFailure.PROVIDER,
            _redact(
                f"TypeSafe returned HTTP {status}: {_provider_detail(payload)}", key
            ),
        )

    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        raise JevError(
            JevFailure.MALFORMED,
            _redact(f"TypeSafe returned non-JSON: {payload[:200]!r}", key),
        ) from None
    if not isinstance(data, dict) or not isinstance(data.get("answers"), dict):
        raise JevError(JevFailure.MALFORMED, "TypeSafe response has no answers map")
    concrete = data.get("model")
    if not isinstance(concrete, str) or not concrete:
        raise JevError(JevFailure.MALFORMED, "TypeSafe response names no model")

    answers: dict[str, ChoiceAnswer] = {}
    for qid, question in questions.items():
        if qid not in data["answers"]:
            raise JevError(JevFailure.MALFORMED, f"TypeSafe omitted answer '{qid}'")
        answers[qid] = _parse_choice(qid, data["answers"][qid], set(question.criteria))

    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    tokens = usage.get("input_tokens")
    return JevResponse(
        model=concrete,
        answers=answers,
        latency_seconds=latency,
        input_tokens=tokens if isinstance(tokens, int) else None,
        request_bytes=len(body),
        raw=data,
    )

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PromptFailureKind(str, Enum):
    MODEL_UNAVAILABLE = "model_unavailable"
    AUTH = "auth"
    RATE_LIMITED = "rate_limited"
    OTHER = "other"


_CLASSIFIED_CLAUDE_STATUSES: dict[int, PromptFailureKind] = {
    404: PromptFailureKind.MODEL_UNAVAILABLE,
    401: PromptFailureKind.AUTH,
    429: PromptFailureKind.RATE_LIMITED,
}

_JUDGE_MODEL_HINT = (
    "Pass `--judge-model <backend[:model]>` to select an available judge model."
)


@dataclass(frozen=True)
class PromptFailure:
    kind: PromptFailureKind
    message: str
    status: int | None = None


def classify_claude_api_error_status(status: int) -> PromptFailureKind | None:
    return _CLASSIFIED_CLAUDE_STATUSES.get(status)


def format_judge_failure(failure: PromptFailure, model: str | None) -> str:
    model_part = f" '{model}'" if model else ""
    if failure.kind is PromptFailureKind.MODEL_UNAVAILABLE:
        return (
            f"Judge model{model_part} is unavailable ({failure.message}). "
            f"{_JUDGE_MODEL_HINT}"
        )
    if failure.kind is PromptFailureKind.AUTH:
        return f"Judge authentication failed ({failure.message}). {_JUDGE_MODEL_HINT}"
    if failure.kind is PromptFailureKind.RATE_LIMITED:
        return (
            f"Judge rate limited ({failure.message}). Try again later or "
            f"pass `--judge-model` to use a different backend."
        )
    return failure.message

from __future__ import annotations

import json
import re

_JUDGE_MODEL_HINT = (
    "Pass `--judge-model <backend[:model]>` to select an available judge model."
)


def _extract_anthropic_not_found_message(text: str) -> str | None:
    for candidate in (text, *_json_object_candidates(text)):
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        error = payload.get("error")
        if isinstance(error, dict) and error.get("type") == "not_found_error":
            message = error.get("message")
            if isinstance(message, str) and message:
                return message
    return None


def _json_object_candidates(text: str) -> list[str]:
    return re.findall(r"\{[^{}]*\}", text)


def is_model_unavailable(text: str) -> bool:
    """Return True when *text* looks like a backend model-resolution failure."""
    if not text.strip():
        return False

    lowered = text.lower()
    if _extract_anthropic_not_found_message(text):
        return True
    if "not_found_error" in lowered:
        return True
    if "model_not_found" in lowered or "model not found" in lowered:
        return True
    if "invalid model" in lowered:
        return True
    if "does not exist" in lowered and (
        re.search(r"\bthe model\b", lowered)
        or ("`" in text and re.search(r"\bmodel\b", lowered))
    ):
        return True
    if re.search(r"\b404\b", text) and re.search(r"\bmodel\b", lowered):
        return True
    return False


def format_model_unavailable_message(text: str, model: str | None) -> str:
    detail = _extract_anthropic_not_found_message(text)
    if detail is None:
        compact = re.sub(r"\s+", " ", text).strip()
        detail = compact[:160] if compact else "model unavailable"

    model_part = f" '{model}'" if model else ""
    return f"Judge model{model_part} is unavailable ({detail}). {_JUDGE_MODEL_HINT}"


def judge_failure_reason(raw: str, model: str | None) -> str:
    if is_model_unavailable(raw):
        return format_model_unavailable_message(raw, model)
    return f"Judge returned unparseable response: {raw[:200]}"

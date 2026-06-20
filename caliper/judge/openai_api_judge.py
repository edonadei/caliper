from __future__ import annotations

from caliper.harness.base import ConversationTurn
from caliper.judge.script_assert import (
    _SYSTEM,
    _USER_TMPL,
    _format_transcript,
    _parse_rich_response,
)


def evaluate_with_openai_api(
    *,
    expect: str,
    transcript: list[ConversationTurn],
    model: str | None,
    spec_dir: str,
    timeout: int = 60,
) -> tuple[bool, str]:
    try:
        from openai import OpenAI
    except ImportError:
        return False, "openai package not installed; run: pip install caliper[openai]"

    user_msg = _USER_TMPL.format(
        expect=expect,
        transcript=_format_transcript(transcript),
    )
    try:
        client = OpenAI()
        response = client.chat.completions.create(
            model=model or "gpt-4o-mini",
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            timeout=timeout,
        )
        raw = response.choices[0].message.content or ""
    except Exception as exc:
        return False, f"openai-api judge failed: {exc}"

    return _parse_rich_response(_strip_markdown_fence(raw.strip()), spec_dir)


def _strip_markdown_fence(raw: str) -> str:
    if not raw.startswith("```"):
        return raw
    lines = raw.splitlines()
    return "\n".join(line for line in lines if not line.startswith("```")).strip()

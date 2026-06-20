from __future__ import annotations

from caliper.harness.base import ConversationTurn
from caliper.judge.script_assert import (
    _SYSTEM,
    _USER_TMPL,
    _format_transcript,
    _parse_rich_response,
)


def evaluate_with_claude_api(
    *,
    expect: str,
    transcript: list[ConversationTurn],
    model: str | None,
    spec_dir: str,
    timeout: int = 60,
) -> tuple[bool, str]:
    import anthropic

    user_msg = _USER_TMPL.format(
        expect=expect,
        transcript=_format_transcript(transcript),
    )

    try:
        client = anthropic.Anthropic(timeout=timeout)
        response = client.messages.create(
            model=model or "claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = response.content[0].text.strip()
    except Exception as exc:
        return False, f"claude-api judge failed: {exc}"

    return _parse_rich_response(raw, spec_dir)

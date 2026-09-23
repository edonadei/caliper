"""Structured evidence projections a classification check sends to Jev.

A projection is a deterministic, documented view of one attempt: the same
transcript always serializes to the same JSON, and each view contains exactly
the events its name promises. Nothing is truncated or summarized here — an
oversized view is the caller's problem to report, not this module's to hide.
"""

from __future__ import annotations

from caliper.harness.base import ConversationTurn


def tool_trace(
    task_prompt: str, transcript: list[ConversationTurn], final_output: str
) -> dict:
    """The task, every tool call and tool result in order, and the final output.

    Assistant prose between tool calls is deliberately left out: the view
    answers "did the answer follow from what the tools returned?", and
    intermediate narration only adds text the classifier has to read past.
    """
    events: list[dict] = []
    for turn in transcript:
        if turn.role == "tool_use":
            events.append(
                {
                    "type": "tool_call",
                    "tool": turn.tool_name or "",
                    "input": turn.tool_input or {},
                }
            )
        elif turn.role == "tool_result":
            events.append(
                {
                    "type": "tool_result",
                    "output": turn.tool_output
                    if turn.tool_output is not None
                    else turn.content,
                }
            )
    return {
        "task_prompt": task_prompt,
        "tool_events": events,
        "final_output": final_output,
    }

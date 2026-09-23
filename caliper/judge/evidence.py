"""Structured evidence views a classification check sends to Jev.

A view is a deterministic, documented projection of one attempt: the same
transcript always serializes to the same JSON, and each view contains the task
prompt, the final output, and exactly the events its name promises. Event order
is preserved, and every tool result stays attached to the call that produced
it. Nothing is truncated or summarized here; an oversized view is reported by
the caller as a judge error, never hidden. See docs/CONTEXT.md → Evidence view.

| view         | events between the prompt and the final output               |
| ------------ | ------------------------------------------------------------ |
| ``output``     | none                                                         |
| ``tool_trace`` | each tool call with its arguments and its result, in order   |
| ``full_trace`` | every normalized conversation event, in order                |
"""

from __future__ import annotations

from typing import Callable

from caliper.harness.base import ConversationTurn


def _result_text(turn: ConversationTurn) -> str:
    return turn.tool_output if turn.tool_output is not None else turn.content


def _numbered_events(transcript: list[ConversationTurn]) -> list[dict]:
    """Every turn as a normalized event, tool calls and results linked by id.

    Transcripts carry no provider call ids, so a result is linked to the oldest
    call still waiting for one, which is the order every backend emits them in.
    A result with no waiting call keeps ``call_id: None`` rather than being
    guessed onto the wrong call.
    """
    events: list[dict] = []
    waiting: list[str] = []
    calls = 0
    for turn in transcript:
        if turn.role == "tool_use":
            calls += 1
            call_id = f"call-{calls}"
            waiting.append(call_id)
            events.append(
                {
                    "type": "tool_call",
                    "call_id": call_id,
                    "tool": turn.tool_name or "",
                    "input": turn.tool_input or {},
                }
            )
        elif turn.role == "tool_result":
            events.append(
                {
                    "type": "tool_result",
                    "call_id": waiting.pop(0) if waiting else None,
                    "output": _result_text(turn),
                }
            )
        else:
            events.append(
                {"type": "message", "role": turn.role, "content": turn.content}
            )
    return events


def output(
    task_prompt: str, transcript: list[ConversationTurn], final_output: str
) -> dict:
    """The task prompt and the final output, and nothing the agent did between."""
    return {"task_prompt": task_prompt, "final_output": final_output}


def tool_trace(
    task_prompt: str, transcript: list[ConversationTurn], final_output: str
) -> dict:
    """The task, each tool call paired with its result in call order, the output.

    Assistant prose between tool calls is left out: this view answers "did the
    answer follow from what the tools returned?", and narration only adds text
    the classifier has to read past. A call that never got a result carries
    ``result: None``; a result with no call is kept, with ``tool: None``.
    """
    tool_calls: list[dict] = []
    by_id: dict[str, dict] = {}
    for event in _numbered_events(transcript):
        if event["type"] == "tool_call":
            call = {"tool": event["tool"], "input": event["input"], "result": None}
            by_id[event["call_id"]] = call
            tool_calls.append(call)
        elif event["type"] == "tool_result":
            if event["call_id"] is None:
                tool_calls.append(
                    {"tool": None, "input": None, "result": event["output"]}
                )
            else:
                by_id[event["call_id"]]["result"] = event["output"]
    return {
        "task_prompt": task_prompt,
        "tool_calls": tool_calls,
        "final_output": final_output,
    }


def full_trace(
    task_prompt: str, transcript: list[ConversationTurn], final_output: str
) -> dict:
    """The task, every normalized conversation event in order, the output."""
    return {
        "task_prompt": task_prompt,
        "events": _numbered_events(transcript),
        "final_output": final_output,
    }


View = Callable[[str, list[ConversationTurn], str], dict]

# The closed enum behind ``evidence:``. Keyed like
# ``caliper.schema.spec.EVIDENCE_VIEWS``, which the schema validates against; a
# test keeps the two identical.
VIEWS: dict[str, View] = {
    "output": output,
    "tool_trace": tool_trace,
    "full_trace": full_trace,
}

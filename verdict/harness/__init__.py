from verdict.harness.base import AttemptResult, ConversationTurn, HarnessBackend
from verdict.harness.claude_code import ClaudeCodeHarness


def get_harness(backend: str, model: str | None = None) -> HarnessBackend:
    match backend:
        case "claude":
            return ClaudeCodeHarness(model=model)
        case "codex":
            from verdict.harness.codex import CodexHarness
            return CodexHarness(model=model)
        case _:
            raise ValueError(f"Unknown backend: {backend!r}")


__all__ = ["AttemptResult", "ConversationTurn", "HarnessBackend", "ClaudeCodeHarness", "get_harness"]

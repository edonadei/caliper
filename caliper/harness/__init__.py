from caliper.harness.base import AttemptResult, ConversationTurn, HarnessBackend
from caliper.harness.claude_code import ClaudeCodeHarness


def get_harness(backend: str, model: str | None = None) -> HarnessBackend:
    match backend:
        case "claude":
            return ClaudeCodeHarness(model=model)
        case "codex":
            from caliper.harness.codex import CodexHarness
            return CodexHarness(model=model)
        case _:
            raise ValueError(f"Unknown backend: {backend!r}")


__all__ = ["AttemptResult", "ConversationTurn", "HarnessBackend", "ClaudeCodeHarness", "get_harness"]

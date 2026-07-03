from caliper.harness.base import (
    AttemptResult,
    CliHarness,
    ConversationTurn,
    HarnessBackend,
)
from caliper.harness.claude_code import ClaudeCodeHarness
from caliper.schema.spec import normalize_backend


def get_harness(backend: str, model: str | None = None) -> HarnessBackend:
    match normalize_backend(backend):
        case "claude-code":
            return ClaudeCodeHarness(model=model)
        case "codex":
            from caliper.harness.codex import CodexHarness

            return CodexHarness(model=model)
        case "pi":
            from caliper.harness.pi import PiHarness

            return PiHarness(model=model)
        case _:
            raise ValueError(f"Unknown backend: {backend!r}")


__all__ = [
    "AttemptResult",
    "ConversationTurn",
    "HarnessBackend",
    "CliHarness",
    "ClaudeCodeHarness",
    "get_harness",
]

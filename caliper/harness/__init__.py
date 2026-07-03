from caliper.harness.base import AttemptResult, ConversationTurn, HarnessBackend
from caliper.harness.claude_code import ClaudeCodeHarness
from caliper.harness.claude_api import ClaudeAPIHarness
from caliper.harness.openai_api import OpenAIAPIHarness
from caliper.schema.spec import normalize_backend


def get_harness(backend: str, model: str | None = None) -> HarnessBackend:
    match normalize_backend(backend):
        case "claude-code":
            return ClaudeCodeHarness(model=model)
        case "codex":
            from caliper.harness.codex import CodexHarness

            return CodexHarness(model=model)
        case "claude-api":
            return ClaudeAPIHarness(model=model)
        case "openai-api":
            return OpenAIAPIHarness(model=model)
        case "pi":
            from caliper.harness.pi import PiHarness

            return PiHarness(model=model)
        case _:
            raise ValueError(f"Unknown backend: {backend!r}")


__all__ = [
    "AttemptResult",
    "ConversationTurn",
    "HarnessBackend",
    "ClaudeCodeHarness",
    "ClaudeAPIHarness",
    "OpenAIAPIHarness",
    "get_harness",
]

from verdict.judge.autorater import AutoraterJudge
from verdict.judge.base import Judge, JudgeResult
from verdict.judge.claude_code_judge import ClaudeCodeJudge
from verdict.judge.codex_judge import CodexJudge


def get_judge(strategy: str, config) -> Judge:
    match strategy:
        case "autorater" | "claude-code":
            if config.backend == "codex":
                return CodexJudge(config)
            # Default is the claude CLI judge; no API key required.
            return ClaudeCodeJudge(config)
        case "autorater-sdk":
            # Explicit opt-in to the SDK-based judge (requires ANTHROPIC_API_KEY).
            return AutoraterJudge(config)
        case "script":
            from verdict.judge.script_assert import ScriptAssertJudge

            return ScriptAssertJudge(config)
        case _:
            raise ValueError(f"Unknown judge strategy: {strategy!r}")


__all__ = [
    "Judge",
    "JudgeResult",
    "AutoraterJudge",
    "ClaudeCodeJudge",
    "CodexJudge",
    "get_judge",
]

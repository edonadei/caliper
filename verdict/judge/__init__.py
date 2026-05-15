import os

from verdict.judge.base import Judge, JudgeResult
from verdict.judge.autorater import AutoraterJudge
from verdict.judge.claude_code_judge import ClaudeCodeJudge


def get_judge(strategy: str, config) -> Judge:
    match strategy:
        case "autorater":
            # Use the claude CLI judge when no API key is available
            if not os.environ.get("ANTHROPIC_API_KEY"):
                return ClaudeCodeJudge(config)
            return AutoraterJudge(config)
        case "claude-code":
            return ClaudeCodeJudge(config)
        case "script":
            from verdict.judge.script_assert import ScriptAssertJudge
            return ScriptAssertJudge(config)
        case _:
            raise ValueError(f"Unknown judge strategy: {strategy!r}")


__all__ = ["Judge", "JudgeResult", "AutoraterJudge", "ClaudeCodeJudge", "get_judge"]

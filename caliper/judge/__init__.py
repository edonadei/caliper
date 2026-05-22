from caliper.judge.autorater import AutoraterJudge
from caliper.judge.base import Judge, JudgeResult
from caliper.judge.claude_code_judge import ClaudeCodeJudge
from caliper.judge.codex_judge import CodexJudge
from caliper.judge.openai_api_judge import OpenAIAPIJudge
from caliper.schema.spec import normalize_backend


def get_judge(strategy: str, config) -> Judge:
    match strategy:
        case "autorater" | "claude-code":
            match normalize_backend(config.backend):
                case "codex":
                    return CodexJudge(config)
                case "claude-code":
                    return ClaudeCodeJudge(config)
                case "claude-api":
                    return AutoraterJudge(config)
                case "openai-api":
                    return OpenAIAPIJudge(config)
                case _:
                    raise ValueError(f"Unknown judge backend: {config.backend!r}")
        case "autorater-sdk":
            # Explicit opt-in to the SDK-based judge (requires ANTHROPIC_API_KEY).
            return AutoraterJudge(config)
        case "script":
            from caliper.judge.script_assert import ScriptAssertJudge

            return ScriptAssertJudge(config)
        case _:
            raise ValueError(f"Unknown judge strategy: {strategy!r}")


__all__ = [
    "Judge",
    "JudgeResult",
    "AutoraterJudge",
    "ClaudeCodeJudge",
    "CodexJudge",
    "OpenAIAPIJudge",
    "get_judge",
]

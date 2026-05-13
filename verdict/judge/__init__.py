from verdict.judge.base import Judge, JudgeResult
from verdict.judge.autorater import AutoraterJudge


def get_judge(strategy: str, config) -> Judge:
    match strategy:
        case "autorater":
            return AutoraterJudge(config)
        case "script":
            from verdict.judge.script_assert import ScriptAssertJudge
            return ScriptAssertJudge(config)
        case _:
            raise ValueError(f"Unknown judge strategy: {strategy!r}")


__all__ = ["Judge", "JudgeResult", "AutoraterJudge", "get_judge"]

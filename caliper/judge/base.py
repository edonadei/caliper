from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from caliper.harness.base import ConversationTurn
from caliper.schema.spec import TaskSpec


@dataclass
class JudgeResult:
    passed: bool
    reasoning: str
    assert_passed: bool | None = None
    assert_evidence: str | None = None
    autorater_passed: bool | None = None
    autorater_reasoning: str | None = None
    # True when the judge could not produce any usable verdict (unparseable
    # autorater response, or the judge call threw). Distinct from a `passed`
    # verdict of False, which is a genuine task failure.
    errored: bool = False


class Judge(ABC):
    strategy: str = "autorater"

    @abstractmethod
    def evaluate(
        self,
        task: TaskSpec,
        transcript: list[ConversationTurn],
        final_output: str,
        spec_dir: str,
    ) -> JudgeResult: ...

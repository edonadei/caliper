from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from verdict.harness.base import ConversationTurn
from verdict.schema.spec import TaskSpec


@dataclass
class JudgeResult:
    passed: bool
    reasoning: str
    assert_passed: bool | None = None
    assert_evidence: str | None = None
    autorater_passed: bool | None = None
    autorater_reasoning: str | None = None


class Judge(ABC):
    @abstractmethod
    def evaluate(
        self,
        task: TaskSpec,
        transcript: list[ConversationTurn],
        final_output: str,
        spec_dir: str,
    ) -> JudgeResult: ...

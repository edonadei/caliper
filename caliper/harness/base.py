from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class HarnessConfigurationError(RuntimeError):
    """Raised when a harness cannot run because local configuration is invalid."""


@dataclass
class ConversationTurn:
    role: str
    content: str
    tool_name: str | None = None
    tool_input: dict | None = None
    tool_output: str | None = None


@dataclass
class AttemptResult:
    task_id: str
    attempt: int
    transcript: list[ConversationTurn]
    final_output: str
    exit_code: int
    duration_seconds: float
    error: str | None = None
    cheated: bool = False
    cheat_evidence: list[str] = field(default_factory=list)


class HarnessBackend(ABC):
    @abstractmethod
    def run(
        self,
        task_id: str,
        attempt: int,
        prompt: str,
        *,
        skill_path: str | None,
        model: str | None,
        timeout: int,
        isolated_home: str,
        extra_path: list[str] | None = None,
    ) -> AttemptResult: ...

    @property
    @abstractmethod
    def name(self) -> str: ...

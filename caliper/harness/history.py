"""A planted history, written as a session the agent's CLI resumes (docs/adr/0036).

Each CLI stores a session in its own format, but planting one is the same work
on all of them: walk the neutral turns, give each planted shell call an id its
result answers, expand a ``/name`` of a declared skill from the installed copy,
chain the messages, and write the file per attempt, since it embeds the
attempt's workdir. This module does that work. A backend declares only what
differs, as a :class:`SessionFormat`: where the file goes, its header, how each
kind of turn is spelled, and how a message is wrapped as a line.

:meth:`SessionFormat.plant` returns the session's id and path; where that goes
in the resume argv stays with the backend's ``_command``.
"""

from __future__ import annotations

import json
import os
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from caliper.skills import declared_slash_command, skill_body

if TYPE_CHECKING:
    from caliper.harness.base import RunContext
    from caliper.schema.spec import PlantedShellCall


@dataclass(frozen=True)
class Session:
    """The session being planted: what a format may write into it."""

    id: str
    # The attempt workdir's real path, which the CLI compares a session's to.
    cwd: str
    # The CLI's own directory in the isolated home, the parent of its skills.
    config_dir: Path
    now: datetime

    @property
    def timestamp(self) -> str:
        return self.now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


@dataclass(frozen=True)
class PlantedSkill:
    """A planted ``/name`` of a declared skill, read from its installed copy.

    Read at run time rather than pasted into the history, so an edit to the
    skill reaches the next run (docs/adr/0036).
    """

    name: str
    # What was typed after the command, or ``""``.
    args: str
    skill_md: Path

    @property
    def text(self) -> str:
        """The whole installed ``SKILL.md``, frontmatter included."""
        return self.skill_md.read_text()

    @property
    def body(self) -> str:
        """The installed skill's instructions, without its frontmatter."""
        return skill_body(self.text)


@dataclass(frozen=True)
class PlantedSession:
    """A planted session on disk: what a resume names it by."""

    id: str
    path: Path


class SessionFormat(ABC):
    """One CLI's session file, declared (docs/adr/0020).

    A message is whatever the format's own hooks return; the module only orders
    them and hands each to :meth:`envelope`. Only the fields a resume was seen
    to need are written: the formats are undocumented CLI internals.
    """

    # The planted shell calls' ids start with this; the rest is the turn's
    # index and the call's, which pairs each call with its result.
    call_prefix: str = "call_planted"

    @abstractmethod
    def path(self, session: Session) -> Path:
        """Where the CLI looks for this session in the isolated home."""

    def header(self, session: Session) -> dict | None:
        """The session's first line, before any message, if the CLI wants one."""
        return None

    @abstractmethod
    def user(self, text: str) -> list[dict]:
        """A user turn as typed, including the command of an ablated skill."""

    @abstractmethod
    def skill(self, skill: PlantedSkill) -> list[dict]:
        """A user turn that typed ``/name`` of an installed skill, as the CLI expands it."""

    @abstractmethod
    def assistant(
        self, text: str | None, calls: list[tuple[str, PlantedShellCall]]
    ) -> list[dict]:
        """An assistant turn and the results of its shell calls, each under its id."""

    @abstractmethod
    def envelope(self, message: dict, previous: dict | None, session: Session) -> dict:
        """``message`` as a line of the file; ``previous`` is the line before it."""

    def plant(
        self, ctx: RunContext, config_home: Path, skills_root: Path
    ) -> PlantedSession:
        """Write ``ctx.history`` as a session for this attempt to resume.

        ``config_home`` is the attempt's (docs/CONTEXT.md → Config home), where
        the CLI keeps its sessions; ``skills_root`` is where a planted ``/name``
        finds the skill it expands.

        A fresh session per call: parallel attempts, and a retried one, never
        share a file. A ``/name`` matches a declared skill exactly, case
        included (:func:`~caliper.skills.declared_slash_command`); an ablated
        one isn't installed, so it stays as typed, as the CLI would leave it.
        """
        session = Session(
            id=str(uuid.uuid4()),
            cwd=os.path.realpath(ctx.workdir),
            config_dir=config_home,
            now=datetime.now(timezone.utc),
        )
        messages: list[dict] = []
        for index, turn in enumerate(ctx.history or []):
            if turn.user is None:
                calls = [
                    (f"{self.call_prefix}_{index}_{i}", call)
                    for i, call in enumerate(turn.shell)
                ]
                messages += self.assistant(turn.assistant, calls)
            elif command := declared_slash_command(turn, ctx.skill_refs):
                name, args = command
                skill_md = skills_root / name / "SKILL.md"
                messages += self.skill(PlantedSkill(name, args, skill_md))
            else:
                messages += self.user(turn.user)
        lines: list[dict] = []
        for message in messages:
            lines.append(self.envelope(message, lines[-1] if lines else None, session))
        header = self.header(session)
        if header is not None:
            lines.insert(0, header)
        path = self.path(session)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(line) + "\n" for line in lines))
        return PlantedSession(session.id, path)

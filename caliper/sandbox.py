"""What the agent-under-test may not touch (docs/CONTEXT.md → Sandbox).

One module owns the whole rule: which paths are off-limits, the ones caliper
adds on the spec author's behalf, how a transcript is scanned for them, and
which of a skill's files are too dangerous to install. The rule used to have
three owners — the runner built the patterns and the detector, ``attempt.py``
declared the shape it needed of that detector, and ``skills.py`` re-compiled the
same patterns for the install.

``RunContext.forbidden_files`` still crosses the harness seam as a plain list of
strings, and deliberately: a backend declares its chores rather than performing
them (docs/adr/0020), so what it carries stays the spec's own data. The
*matching* is asked of this module at both ends of that wire.

Two audiences, deliberately not the same list:

- :meth:`SpecSandbox.violations` scans a finished transcript, and uses the
  declared patterns **plus** the auto-forbidden ones (the spec file, any saved
  results) — the answer keys an author should not have to think to declare.
- :meth:`SpecSandbox.permits_install` filters a skill's files at install time,
  and uses the declared patterns **alone**. The auto-forbidden entries are
  absolute host paths; matching them against a skill's relative install paths
  could only ever fire by accident.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:  # Import-time only: harness.base reaches back into skills.py,
    # which imports this module, so the runtime import would close a cycle.
    from caliper.harness.base import ConversationTurn
    from caliper.schema.spec import EvalSpec

from caliper.runstore import CALIPER_DIR, RESULTS_DIR

# How deep into an agent-supplied ``tool_input`` the scan walks. The input is
# arbitrary JSON the agent wrote, so the walk is bounded rather than trusted.
_MAX_DEPTH = 5

#: Any saved run, wherever it is filed — a real regex, unlike the ``auto``
#: entries, and the only forbidden rule that is not a resolved path.
#:
#: It cannot be one. The results root is *discovered* rather than fixed
#: (docs/adr/0022-saved-runs-live-at-a-discovered-results-root.md), so resolving
#: it would forbid the one location this run happens to write to and leave every
#: other readable: an older root, another checkout's, the one a monorepo sibling
#: owns. Reading last run's results is reading the answer key wherever they are.
#:
#: Narrow on both sides on purpose. It matches ``.caliper/results/`` and not
#: ``.caliper`` alone, because a task may legitimately *write a caliper spec*
#: whose text contains ``./.caliper/.*`` — grill-skill's own eval does, and a
#: bare marker flagged those attempts as cheats when the pattern was declared by
#: hand. The leading boundary keeps ``.caliper-mcp.json``, the MCP config the
#: harness writes into the agent's own home, out of it.
SAVED_RESULTS = (
    rf"(?<![\w.-]){re.escape(CALIPER_DIR)}[/\\]{re.escape(RESULTS_DIR)}[/\\]"
)


class Sandbox(Protocol):
    """What grading needs of a sandbox: the violations in a transcript.

    A structural seam, like :class:`caliper.judge.base.Judge` — there is one
    production implementation (:class:`SpecSandbox`), and a test double conforms
    by shape.
    """

    def violations(self, transcript: list[ConversationTurn]) -> list[str]: ...


@dataclass(frozen=True)
class SpecSandbox:
    """The sandbox an eval spec describes, plus caliper's own additions.

    ``declared`` is verbatim ``sandbox.forbidden_files`` — regexes, written by
    the spec author. ``auto`` is the *paths* caliper forbids on every run; the
    entries are real paths, so they are escaped into literal patterns rather
    than honoured as regexes (a spec living at ``demo+v2.eval.yaml`` must match
    itself, not a repetition). :data:`SAVED_RESULTS` is forbidden on top of
    both, always: it is a rule about a directory *shape*, not a location, so
    there is no path for it to be an entry of.
    """

    declared: list[str] = field(default_factory=list)
    auto: list[str] = field(default_factory=list)

    @classmethod
    def from_spec(cls, spec: EvalSpec, spec_path: Path) -> SpecSandbox:
        """The sandbox for a run of ``spec``, loaded from ``spec_path``.

        Two answer keys are forbidden without the author declaring them: the
        spec file, which holds every ``expect:``, and any saved run — the
        latter via :data:`SAVED_RESULTS` rather than a path, because the results
        root is discovered and a run may be filed under one this call never
        resolved.
        """
        return cls(
            declared=list(spec.sandbox.forbidden_files),
            auto=[str(spec_path.resolve())],
        )

    def violations(self, transcript: list[ConversationTurn]) -> list[str]:
        """Every path in ``transcript`` that the sandbox forbids.

        Reported rather than counted: the record keeps the offending paths as
        the evidence behind a ``cheat`` outcome (docs/adr/0001).
        """
        patterns = self._compiled
        found: list[str] = []
        for turn in transcript:
            if not turn.tool_input:
                continue
            for value in _paths_in(turn.tool_input):
                if any(p.search(value) for p in patterns):
                    found.append(value)
        return found

    def permits_install(self, rel_posix: str) -> bool:
        """Whether a skill file at this install-relative path may be installed.

        Matched twice, bare and ``./``-prefixed, because both are how a spec
        author writes a repo-relative path and neither should silently miss.
        """
        return not any(
            p.search(rel_posix) or p.search("./" + rel_posix)
            for p in self._declared_compiled
        )

    # Compiled once per sandbox, not once per call: ``permits_install`` runs for
    # every file of every installed skill. ``cached_property`` writes straight
    # into the instance dict, which a frozen dataclass still permits.
    @cached_property
    def _declared_compiled(self) -> list[re.Pattern[str]]:
        return [re.compile(p) for p in self.declared]

    @cached_property
    def _compiled(self) -> list[re.Pattern[str]]:
        return [
            *self._declared_compiled,
            *(re.compile(re.escape(p)) for p in self.auto),
            re.compile(SAVED_RESULTS),
        ]


def _paths_in(obj: object, depth: int = 0) -> list[str]:
    """Every string in ``obj`` that could name a path.

    A string qualifies on containing a ``/`` or a ``.`` — deliberately loose,
    because a missed candidate is a missed cheat, while a false candidate only
    reaches the patterns and fails to match.
    """
    if depth > _MAX_DEPTH:
        return []
    if isinstance(obj, str):
        return [obj] if ("/" in obj or "." in obj) else []
    if isinstance(obj, dict):
        found: list[str] = []
        for value in obj.values():
            found.extend(_paths_in(value, depth + 1))
        return found
    if isinstance(obj, list):
        found = []
        for item in obj:
            found.extend(_paths_in(item, depth + 1))
        return found
    return []

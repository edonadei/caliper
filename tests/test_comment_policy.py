"""How comments in caliper/ are written: short, and linked to the record.

See AGENTS.md -> Comments.
"""

from __future__ import annotations

import re
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# A longer run of full-line comments is an essay. Its reasoning belongs in an
# ADR or docs/CONTEXT.md, and the comment should link it.
MAX_COMMENT_BLOCK_LINES = 5

# Files that already hold longer blocks, and how many. A file may not gain one;
# when it loses one, lower its count here, so the ceiling only ever tightens.
LONG_COMMENT_BLOCK_ALLOWANCE: dict[str, int] = {
    "caliper/commands/list_cmd.py": 1,
    "caliper/commands/run.py": 1,
    "caliper/commands/validate.py": 1,
    "caliper/environment.py": 1,
    "caliper/harness/base.py": 4,
    "caliper/harness/claude_code.py": 1,
    "caliper/harness/hermes.py": 2,
    "caliper/harness/pi.py": 1,
    "caliper/harness/refusal.py": 1,
    "caliper/judge/eval_judge.py": 1,
    "caliper/runner.py": 3,
    "caliper/sandbox.py": 1,
    "caliper/schema/results.py": 11,
    "caliper/schema/spec.py": 1,
    "caliper/skills.py": 1,
}

_MARKER = re.compile(r"\b(?:TODO|FIXME|XXX|HACK)\b")
_ISSUE_REF = re.compile(r"#\d+")
_NOQA = re.compile(r"#\s*noqa\b(.*)", re.IGNORECASE)
_RULES_AND_REASON = re.compile(r":\s*[A-Z]+\d+(?:\s*,\s*[A-Z]+\d+)*\s+[-—–]\s*\S.*")


def _comments(path: Path) -> list[tuple[int, str, bool]]:
    """Each comment in a module: its line, its text, and whether it fills the line."""
    found: list[tuple[int, str, bool]] = []
    with path.open("rb") as source:
        for tok in tokenize.tokenize(source.readline):
            if tok.type == tokenize.COMMENT:
                full_line = tok.line.lstrip().startswith("#")
                found.append((tok.start[0], tok.string, full_line))
    return found


def _modules() -> list[tuple[str, list[tuple[int, str, bool]]]]:
    return [
        (path.relative_to(ROOT).as_posix(), _comments(path))
        for path in sorted((ROOT / "caliper").rglob("*.py"))
    ]


def _long_blocks(comments: list[tuple[int, str, bool]]) -> list[tuple[int, int]]:
    """(first line, length) of each run of full-line comments over the ceiling."""
    runs: list[list[int]] = []
    for line, _, full_line in comments:
        if not full_line:
            continue
        if runs and runs[-1][-1] == line - 1:
            runs[-1].append(line)
        else:
            runs.append([line])
    return [(run[0], len(run)) for run in runs if len(run) > MAX_COMMENT_BLOCK_LINES]


def test_comment_blocks_stay_short() -> None:
    grown: list[str] = []
    shrunk: list[str] = []
    modules = _modules()
    for rel, comments in modules:
        blocks = _long_blocks(comments)
        allowed = LONG_COMMENT_BLOCK_ALLOWANCE.get(rel, 0)
        if len(blocks) > allowed:
            grown += [f"{rel}:{line} ({length} lines)" for line, length in blocks]
        elif len(blocks) < allowed:
            shrunk.append(f"{rel}: {allowed} -> {len(blocks)}")

    assert grown == [], (
        f"comment blocks longer than {MAX_COMMENT_BLOCK_LINES} lines, in a file "
        "that has more of them than LONG_COMMENT_BLOCK_ALLOWANCE allows. Move the "
        "reasoning to docs/adr/ or docs/CONTEXT.md and leave a short comment "
        "that links it (`see docs/adr/NNNN-slug.md`):\n" + "\n".join(grown)
    )
    missing = sorted(LONG_COMMENT_BLOCK_ALLOWANCE.keys() - {rel for rel, _ in modules})
    assert shrunk == [], (
        "LONG_COMMENT_BLOCK_ALLOWANCE allows more than the code needs. Lower "
        f"these counts, dropping an entry at 0: {shrunk}"
    )
    assert missing == [], (
        f"LONG_COMMENT_BLOCK_ALLOWANCE names files that do not exist: {missing}"
    )


def test_work_markers_cite_an_issue() -> None:
    unlinked = [
        f"{rel}:{line}: {text}"
        for rel, comments in _modules()
        for line, text, _ in comments
        if _MARKER.search(text) and not _ISSUE_REF.search(text)
    ]
    assert unlinked == [], (
        "a TODO, FIXME, XXX or HACK comment must cite the issue that tracks it "
        "(`# TODO(#123): ...`), or the work should be done now:\n" + "\n".join(unlinked)
    )


def test_noqa_names_its_rules_and_a_reason() -> None:
    bare = [
        f"{rel}:{line}: {text}"
        for rel, comments in _modules()
        for line, text, _ in comments
        if (noqa := _NOQA.search(text)) and not _RULES_AND_REASON.match(noqa.group(1))
    ]
    assert bare == [], (
        "a `# noqa` must name its rules and say why they do not apply here "
        "(`# noqa: BLE001 - optional provenance must not fail the attempt`):\n"
        + "\n".join(bare)
    )

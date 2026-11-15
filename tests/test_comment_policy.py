"""How comments in caliper/ are written: short, and linked to the record.

See AGENTS.md -> Comments.
"""

from __future__ import annotations

import re
import tokenize
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parent.parent

# A longer run of full-line comments is an essay. Its reasoning belongs in an
# ADR or docs/CONTEXT.md, and the comment should link it.
MAX_COMMENT_BLOCK_LINES = 5

# Files that already hold longer blocks: each block's length, longest first.
# A file may not gain a block or grow one. When one shrinks, lower its length
# here (drop it at the ceiling), so the allowance only ever tightens.
LONG_COMMENT_BLOCK_ALLOWANCE: dict[str, tuple[int, ...]] = {
    "caliper/commands/list_cmd.py": (7,),
    "caliper/commands/run.py": (6,),
    "caliper/commands/validate.py": (7,),
    "caliper/environment.py": (6,),
    "caliper/harness/base.py": (8, 7, 7, 6),
    "caliper/harness/claude_code.py": (6,),
    "caliper/harness/hermes.py": (7, 6),
    "caliper/harness/pi.py": (10,),
    "caliper/harness/refusal.py": (7,),
    "caliper/judge/eval_judge.py": (6,),
    "caliper/runner.py": (6, 6, 6),
    "caliper/sandbox.py": (15,),
    "caliper/schema/results.py": (9, 8, 7, 7, 7, 6, 6, 6, 6, 6, 6),
    "caliper/schema/spec.py": (7,),
    "caliper/skills.py": (6,),
}

# A marker not followed by its issue, as in `TODO(#123)`.
_UNLINKED_MARKER = re.compile(r"\b(?:TODO|FIXME|XXX|HACK)\b(?!\(#\d+\))")
# A noqa directive, line- or file-level, and whatever follows it.
_NOQA = re.compile(r"#\s*(?:ruff:\s*)?noqa\b(.*)", re.IGNORECASE)
_RULES_AND_REASON = re.compile(r":\s*[A-Z]+\d+(?:\s*,\s*[A-Z]+\d+)*\s+[-—–]\s*\S.*")


class _Comment(NamedTuple):
    line: int
    text: str
    # Nothing but indentation before it, so it can belong to a block.
    full_line: bool


def _comments(path: Path) -> list[_Comment]:
    source_lines = path.read_text(encoding="utf-8").splitlines()
    found: list[_Comment] = []
    with path.open("rb") as source:
        for tok in tokenize.tokenize(source.readline):
            if tok.type == tokenize.COMMENT:
                line, column = tok.start
                before = source_lines[line - 1][:column]
                found.append(_Comment(line, tok.string, not before.strip()))
    return found


def _modules() -> list[tuple[str, list[_Comment]]]:
    return [
        (path.relative_to(ROOT).as_posix(), _comments(path))
        for path in sorted((ROOT / "caliper").rglob("*.py"))
    ]


def _long_blocks(comments: list[_Comment]) -> list[tuple[int, int]]:
    """(first line, length) of each run of full-line comments over the ceiling."""
    runs: list[list[int]] = []
    for comment in comments:
        if not comment.full_line:
            continue
        if runs and runs[-1][-1] == comment.line - 1:
            runs[-1].append(comment.line)
        else:
            runs.append([comment.line])
    return [(run[0], len(run)) for run in runs if len(run) > MAX_COMMENT_BLOCK_LINES]


def test_comment_blocks_stay_short() -> None:
    modules = _modules()
    missing = sorted(LONG_COMMENT_BLOCK_ALLOWANCE.keys() - {rel for rel, _ in modules})
    assert missing == [], (
        f"LONG_COMMENT_BLOCK_ALLOWANCE names files that do not exist: {missing}"
    )

    grown: list[str] = []
    shrunk: list[str] = []
    for rel, comments in modules:
        blocks = _long_blocks(comments)
        lengths = tuple(sorted((length for _, length in blocks), reverse=True))
        allowed = LONG_COMMENT_BLOCK_ALLOWANCE.get(rel, ())
        within = len(lengths) <= len(allowed) and all(
            length <= cap for length, cap in zip(lengths, allowed, strict=False)
        )
        if not within:
            grown += [f"{rel}:{line} ({length} lines)" for line, length in blocks]
        elif lengths != allowed:
            shrunk.append(f"{rel}: {allowed} -> {lengths}")

    assert grown == [], (
        f"comment blocks longer than {MAX_COMMENT_BLOCK_LINES} lines, in a file "
        "whose blocks LONG_COMMENT_BLOCK_ALLOWANCE does not cover. Move the "
        "reasoning to docs/adr/ or docs/CONTEXT.md and leave a short comment "
        "that links it (`(docs/adr/NNNN)`):\n" + "\n".join(grown)
    )
    assert shrunk == [], (
        "LONG_COMMENT_BLOCK_ALLOWANCE allows more than the code needs. Set these "
        f"entries to the new lengths, dropping one left empty: {shrunk}"
    )


def test_work_markers_cite_an_issue() -> None:
    unlinked = [
        f"{rel}:{comment.line}: {comment.text}"
        for rel, comments in _modules()
        for comment in comments
        if _UNLINKED_MARKER.search(comment.text)
    ]
    assert unlinked == [], (
        "a TODO, FIXME, XXX or HACK comment must cite the issue that tracks it "
        "(`# TODO(#123): ...`), or the work should be done now:\n" + "\n".join(unlinked)
    )


def test_noqa_names_its_rules_and_a_reason() -> None:
    bare = [
        f"{rel}:{comment.line}: {comment.text}"
        for rel, comments in _modules()
        for comment in comments
        if (noqa := _NOQA.search(comment.text))
        and not _RULES_AND_REASON.match(noqa.group(1))
    ]
    assert bare == [], (
        "a `# noqa` must name its rules and say why they do not apply here "
        "(`# noqa: BLE001 - optional provenance must not fail the attempt`):\n"
        + "\n".join(bare)
    )

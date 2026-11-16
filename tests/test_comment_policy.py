"""How comments in caliper/ are written: short, and linked to the record.

See AGENTS.md -> Comments.
"""

from __future__ import annotations

import ast
import re
import sys
import tokenize
from pathlib import Path
from typing import NamedTuple

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

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

# Files that already hold longer docstrings, on the same terms as the comment
# blocks above.
LONG_DOCSTRING_ALLOWANCE: dict[str, tuple[int, ...]] = {
    "caliper/activation.py": (22, 8),
    "caliper/attempt.py": (23, 16, 13, 10, 8),
    "caliper/backends.py": (7, 7),
    "caliper/cancel.py": (16, 8, 7, 7, 7),
    "caliper/chatgpt.py": (8,),
    "caliper/commands/compare.py": (13,),
    "caliper/commands/diagnosis.py": (18, 10),
    "caliper/commands/engine.py": (6,),
    "caliper/commands/list_cmd.py": (6, 6),
    "caliper/commands/run.py": (13, 9),
    "caliper/compare.py": (17, 9, 7, 7, 7, 7, 7, 6),
    "caliper/environment.py": (14, 12, 6),
    "caliper/harness/antigravity.py": (8, 7, 6),
    "caliper/harness/base.py": (
        19,
        14,
        12,
        11,
        9,
        9,
        8,
        8,
        8,
        8,
        8,
        7,
        7,
        7,
        7,
        7,
        7,
        7,
        6,
        6,
        6,
        6,
        6,
        6,
        6,
    ),
    "caliper/harness/claude_code.py": (13, 7, 7, 6),
    "caliper/harness/cli_config.py": (18,),
    "caliper/harness/codex.py": (9, 9, 7, 6, 6, 6, 6),
    "caliper/harness/hermes.py": (19, 8, 7, 6, 6),
    "caliper/harness/history.py": (13, 10),
    "caliper/harness/mcp.py": (17, 10, 6, 6),
    "caliper/harness/opencode.py": (9, 6, 6),
    "caliper/harness/pi.py": (9, 8, 7, 6),
    "caliper/harness/refusal.py": (9,),
    "caliper/harness/user_layer.py": (11, 7, 7),
    "caliper/judge/base.py": (21,),
    "caliper/judge/eval_judge.py": (10,),
    "caliper/reporter.py": (23, 8, 6, 6, 6, 6, 6),
    "caliper/retry.py": (22, 21, 11),
    "caliper/runner.py": (11, 11, 9, 9, 7),
    "caliper/runstore.py": (25, 19, 11, 8, 6, 6),
    "caliper/sandbox.py": (23, 9, 7, 6),
    "caliper/schema/results.py": (
        17,
        11,
        11,
        11,
        10,
        9,
        9,
        9,
        8,
        8,
        7,
        7,
        7,
        6,
        6,
        6,
        6,
        6,
        6,
        6,
        6,
        6,
        6,
    ),
    "caliper/schema/spec.py": (10, 7, 6),
    "caliper/skillfetch.py": (13, 11, 9, 7, 7, 7, 6, 6),
    "caliper/skills.py": (21, 18, 14, 13, 12, 11, 11, 8, 8, 7, 6),
    "caliper/skillsnapshot.py": (10, 6),
    "caliper/telemetry.py": (6,),
    "caliper/workdir.py": (9, 8),
}

_DOCUMENTED = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)

# A marker not followed by its issue, as in `TODO(#123)`.
_UNLINKED_MARKER = re.compile(r"\b(?:(?i:todo|fixme)|XXX|HACK)\b(?!\(#\d+\))")
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


def _docstrings(path: Path) -> list[tuple[int, int]]:
    """(first line, text lines) of each docstring over the ceiling."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, _DOCUMENTED):
            continue
        doc = ast.get_docstring(node, clean=False)
        length = len(doc.strip().splitlines()) if doc else 0
        if length > MAX_COMMENT_BLOCK_LINES:
            found.append((node.body[0].lineno, length))
    return found


def _ratchet(
    blocks: dict[str, list[tuple[int, int]]], allowance: dict[str, tuple[int, ...]]
) -> tuple[list[str], list[str]]:
    """Blocks no allowance covers, and allowances the code no longer needs."""
    missing = sorted(allowance.keys() - blocks.keys())
    assert missing == [], f"the allowance names files that do not exist: {missing}"
    grown: list[str] = []
    shrunk: list[str] = []
    for rel, found in blocks.items():
        lengths = tuple(sorted((length for _, length in found), reverse=True))
        allowed = allowance.get(rel, ())
        within = len(lengths) <= len(allowed) and all(
            length <= cap for length, cap in zip(lengths, allowed, strict=False)
        )
        if not within:
            grown += [f"{rel}:{line} ({length} lines)" for line, length in found]
        elif lengths != allowed:
            shrunk.append(f"{rel}: {allowed} -> {lengths}")
    return grown, shrunk


def test_comment_blocks_stay_short() -> None:
    grown, shrunk = _ratchet(
        {rel: _long_blocks(comments) for rel, comments in _modules()},
        LONG_COMMENT_BLOCK_ALLOWANCE,
    )
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


def test_docstrings_stay_short() -> None:
    grown, shrunk = _ratchet(
        {
            path.relative_to(ROOT).as_posix(): _docstrings(path)
            for path in sorted((ROOT / "caliper").rglob("*.py"))
        },
        LONG_DOCSTRING_ALLOWANCE,
    )
    assert grown == [], (
        f"docstrings longer than {MAX_COMMENT_BLOCK_LINES} lines, in a file whose "
        "docstrings LONG_DOCSTRING_ALLOWANCE does not cover. Say what the code "
        "does in a few lines, and link the record that holds the why:\n"
        + "\n".join(grown)
    )
    assert shrunk == [], (
        "LONG_DOCSTRING_ALLOWANCE allows more than the code needs. Set these "
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


# ty's scope (pyproject.toml [tool.ty.src]), where docs/adr/0048 allows no
# suppressions at all.
_TYPE_IGNORE = re.compile(r"#\s*(?:type|ty)\s*:\s*ignore\b")


def test_type_checking_is_never_suppressed() -> None:
    with (ROOT / "pyproject.toml").open("rb") as f:
        scope = tomllib.load(f)["tool"]["ty"]["src"]["include"]
    suppressed = [
        f"{path.relative_to(ROOT).as_posix()}:{comment.line}: {comment.text}"
        for folder in scope
        for path in sorted((ROOT / folder).rglob("*.py"))
        for comment in _comments(path)
        if _TYPE_IGNORE.search(comment.text)
    ]
    assert suppressed == [], (
        "ty runs with zero suppressions (docs/adr/0048). Fix the type the "
        "checker reports instead of ignoring it:\n" + "\n".join(suppressed)
    )

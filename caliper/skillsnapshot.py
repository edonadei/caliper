"""Capturing what a member of the [[skill neighbourhood]] *was* when a run used it.

A snapshot is the run's own record of the text that produced its score: every
file the install copied (``SKILL.md`` and whatever travels with it, referenced
or not) and the provenance of the whole. It is what ``compare`` reads to report
[[skill drift]] — see docs/CONTEXT.md → Skill drift and docs/adr/0017.

Lives beside :mod:`caliper.skillfetch` rather than in the runner: a git source
already knows its commit because the fetcher resolved it, and only a *path*
source has to be interrogated with ``git`` at all.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from caliper.sandbox import SpecSandbox
from caliper.schema.results import FileSnapshot, SkillSnapshot
from caliper.skills import SkillRef, installed_files


def snapshot_skill(
    ref: SkillRef, forbidden_files: list[str] | None = None
) -> SkillSnapshot:
    """Capture ``ref``'s files and provenance as they are right now.

    The files are exactly those the install copies, asked of
    :func:`caliper.skills.installed_files` rather than re-derived, so the
    snapshot cannot disagree with what the agent could read. ``forbidden_files``
    is the spec's ``sandbox.forbidden_files``, which the install honours too.
    """
    path = Path(ref.path).expanduser().resolve()
    if not path.exists():
        return SkillSnapshot(
            name=ref.name, path=str(path), source_kind=ref.source_kind, files={}
        )

    sandbox = SpecSandbox(declared=list(forbidden_files or []))
    files = {
        rel.as_posix(): _file_snapshot((ref.directory / rel).read_bytes())
        for rel in installed_files(ref.directory, sandbox)
    }

    # A git source already knows its provenance exactly — caliper resolved the
    # ref and cloned that commit — so it is taken from the ref rather than
    # re-derived from the checkout, whose HEAD is the same thing by a longer
    # route. Only a path source has to be interrogated.
    if ref.source_kind == "git":
        git_repo, git_sha = ref.git_repo, ref.git_sha
    else:
        git_repo, git_sha = _git_info(path)

    return SkillSnapshot(
        name=ref.name,
        path=str(path),
        source_kind=ref.source_kind,
        git_repo=git_repo,
        git_sha=git_sha,
        files=files,
    )


def _file_snapshot(data: bytes) -> FileSnapshot:
    # Hashed as bytes because the install copies bytes: a line-ending change is
    # drift the agent would see.
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError:
        content = None
    return FileSnapshot(
        content=content, hash="sha256:" + hashlib.sha256(data).hexdigest()
    )


def _git_info(path: Path) -> tuple[str | None, str | None]:
    """The repo and commit a path source sits at, or ``(None, None)``."""
    try:
        repo = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(path.parent),
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(path.parent),
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return repo, sha
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None, None

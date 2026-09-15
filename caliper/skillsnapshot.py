"""Capturing what a member of the [[skill neighbourhood]] *was* when a run used it.

A snapshot is the run's own record of the text that produced its score: the
``SKILL.md``, the companion files it points at (progressive disclosure is the
normal shape), and the provenance of the whole. It is what ``compare`` reads to
report [[skill drift]] — see docs/CONTEXT.md → Skill drift and docs/adr/0017.

Lives beside :mod:`caliper.skillfetch` rather than in the runner: a git source
already knows its commit because the fetcher resolved it, and only a *path*
source has to be interrogated with ``git`` at all.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path

from caliper.schema.results import FileSnapshot, SkillSnapshot
from caliper.skills import SkillRef

# A relative or home-anchored pointer to a companion file, as a SKILL.md writes
# one: `./REFERENCE.md`, `references/style.md`, `~/bin/check.sh`.
_REF_PATTERN = re.compile(r'[./~][^\s"\'<>]+\.(sh|py|md|js|ts)')


def snapshot_skill(ref: SkillRef) -> SkillSnapshot:
    """Capture ``ref``'s files and provenance as they are right now."""
    path = Path(ref.path).expanduser().resolve()
    if not path.exists():
        return SkillSnapshot(
            name=ref.name, path=str(path), source_kind=ref.source_kind, files={}
        )

    # The directory `install_skills` actually walks: ``SkillRef.directory``, the
    # parent as the spec *named* it. Resolving it — as `path` above must, for
    # provenance — disagrees with the installer whenever a skill is reached
    # through a symlinked directory (`~/.claude/skills/foo` -> a repo), and a
    # reference written in the link's own terms would then be dropped from the
    # snapshot though the run installed it.
    directory = Path(os.path.abspath(Path(ref.path).expanduser().parent))

    content = path.read_text()
    files: dict[str, FileSnapshot] = {path.name: _file_snapshot(content)}

    # `referenced`, not `ref`: the parameter is the SkillRef, and reusing the
    # name here silently rebound it to a Path for every skill whose SKILL.md
    # points at a companion file — which is most real ones.
    for match in _REF_PATTERN.finditer(content):
        referenced = Path(match.group()).expanduser()
        if not referenced.is_absolute():
            referenced = directory / referenced
        # Normalise `..` without resolving symlinks: install_skills copies a
        # file symlink's bytes to the link's own name, so the installed path —
        # not the link's target — is the path the run measured. Resolving here
        # would drop an in-directory companion that points elsewhere, and its
        # later changes would go unreported as drift.
        referenced = Path(os.path.normpath(referenced))
        if not referenced.exists() or referenced.resolve() == path:
            continue
        if not referenced.is_relative_to(directory):
            # A SKILL.md can point outside its own directory — a shared style
            # guide, say. Only the skill directory is installed, so a file
            # outside it is not part of what the run measured (see
            # docs/CONTEXT.md → Progressive disclosure).
            continue
        rel = referenced.relative_to(directory)
        if _reached_through_directory_symlink(directory, rel):
            continue
        files[str(rel)] = _file_snapshot(referenced.read_text())

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


def _reached_through_directory_symlink(directory: Path, rel: Path) -> bool:
    """Whether reaching ``rel`` from ``directory`` crosses a linked directory.

    ``install_skills`` walks with ``rglob``, which does not descend directory
    symlinks: it sees the link, never the files beneath it. A reference reached
    through one is therefore never installed, and bytes the agent never received
    must not enter the snapshot as drift.
    """
    return any(
        (directory / Path(*rel.parts[:i])).is_symlink()
        for i in range(1, len(rel.parts))
    )


def _file_snapshot(content: str) -> FileSnapshot:
    return FileSnapshot(
        content=content,
        hash="sha256:" + hashlib.sha256(content.encode()).hexdigest(),
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

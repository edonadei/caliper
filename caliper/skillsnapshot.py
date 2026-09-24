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

from caliper.sandbox import SpecSandbox
from caliper.schema.results import FileSnapshot, SkillSnapshot
from caliper.skills import SkillRef, _installable_skill_file

# A relative or home-anchored pointer to a companion file, as a SKILL.md writes
# one: `./REFERENCE.md`, `references/style.md`, `~/bin/check.sh`.
_REF_PATTERN = re.compile(r'[./~][^\s"\'<>]+\.(sh|py|md|js|ts)')


def snapshot_skill(
    ref: SkillRef, forbidden_files: list[str] | None = None
) -> SkillSnapshot:
    """Capture ``ref``'s files and provenance as they are right now."""
    path = Path(ref.path).expanduser().resolve()
    if not path.exists():
        return SkillSnapshot(
            name=ref.name, path=str(path), source_kind=ref.source_kind, files={}
        )

    # Use the skill directory exactly as the spec wrote it, symlinks left in
    # place. That is the directory `install_skills` copies from, so it is the
    # one that decides which files the run actually saw. `path` above is
    # resolved because provenance wants the real location, but if we judged
    # references against that resolved path, a skill living behind a symlinked
    # directory (`~/.claude/skills/foo` -> some repo) would lose references
    # written with the symlink's name, even though they were installed.
    directory = Path(os.path.abspath(Path(ref.path).expanduser().parent))
    source_root = directory.resolve()
    sandbox = SpecSandbox(declared=list(forbidden_files or []))

    content = path.read_text()
    primary = _installable_skill_file(
        directory / path.name, source_root, Path(path.name), sandbox
    )
    files: dict[str, FileSnapshot] = (
        {path.name: _file_snapshot(content)} if primary is not None else {}
    )

    # `referenced`, not `ref`: the parameter is the SkillRef, and reusing the
    # name here silently rebound it to a Path for every skill whose SKILL.md
    # points at a companion file — which is most real ones.
    for match in _REF_PATTERN.finditer(content):
        referenced = Path(match.group()).expanduser()
        if not referenced.is_absolute():
            referenced = directory / referenced
        # Clean up `..` segments but keep the link's path as the installed
        # name. Internal file symlinks are copied under that name.
        referenced = Path(os.path.normpath(referenced))
        # Skip the SKILL.md itself by comparing paths as written, not by what
        # they resolve to. An `alias.md -> SKILL.md` symlink is installed as a
        # second file, and a change to it later is real drift, so it must stay.
        if not referenced.exists() or referenced == directory / Path(ref.path).name:
            continue
        rel = _installed_relative_path(directory, referenced)
        if rel is None:
            # The reference points outside the skill directory (a shared style
            # guide, for example). Only the skill directory is installed, so
            # the run never saw that file and it does not belong in the
            # snapshot. See docs/CONTEXT.md → Progressive disclosure.
            continue
        if _reached_through_directory_symlink(directory, rel):
            # Files under a symlinked directory are never installed either.
            continue
        source = _installable_skill_file(referenced, source_root, rel, sandbox)
        if source is None:
            # The install skips external or forbidden targets, so a snapshot
            # cannot record their contents either.
            continue
        files[str(rel)] = _file_snapshot(source.read_text())

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


def _installed_relative_path(directory: Path, referenced: Path) -> Path | None:
    """Return the path of ``referenced`` relative to the installed skill.

    Returns ``None`` when the file is not inside the skill directory.

    When the skill directory is a symlink (`~/.claude/skills/foo` -> some
    repo), the same file has two valid absolute paths: one through the link
    and one through the real location. A SKILL.md may use either, and both
    are installed at the same relative path, so both are accepted here.

    The real-location form gets one extra check: the file must also exist at
    that relative path under ``directory`` and be the same file. Otherwise a
    symlink somewhere in the middle could make the two paths point at
    different things.
    """
    if referenced.is_relative_to(directory):
        return referenced.relative_to(directory)
    resolved = Path(os.path.normpath(directory.resolve()))
    if not referenced.is_relative_to(resolved):
        return None
    rel = referenced.relative_to(resolved)
    installed = directory / rel
    if installed.exists() and installed.resolve() == referenced.resolve():
        return rel
    return None


def _reached_through_directory_symlink(directory: Path, rel: Path) -> bool:
    """Return True if any directory on the way to ``rel`` is a symlink.

    ``install_skills`` uses ``rglob``, which does not follow directory
    symlinks. A file below one is never installed, so the agent never saw it
    and the snapshot must not track it.
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

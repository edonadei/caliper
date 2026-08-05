"""Snapshotting a neighbourhood member — files captured, provenance recorded.

See docs/CONTEXT.md → Skill drift and
docs/adr/0017-unpinned-git-sources-are-allowed-because-drift-is-reported.md.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from caliper.skills import SkillRef
from caliper.skillsnapshot import snapshot_skill


def _git(*args: str, cwd: Path) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=str(cwd), text=True, stderr=subprocess.DEVNULL
    ).strip()


def _write_skill(directory: Path, name: str, body: str | None = None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "SKILL.md"
    path.write_text(
        body
        if body is not None
        else f"---\nname: {name}\ndescription: A skill for testing.\n---\n\nBody.\n"
    )
    return path


def test_a_skill_referencing_companion_files_still_snapshots(tmp_path: Path):
    """Progressive disclosure is the normal shape, so the snapshot must survive it.

    Every other snapshot test uses a SKILL.md that points at nothing, which is
    what let a name collision in the referenced-file loop reach a real run.
    """
    directory = tmp_path / "mine"
    directory.mkdir()
    (directory / "REFERENCE.md").write_text("# Reference\n")
    (directory / "SKILL.md").write_text(
        "---\nname: mine\ndescription: d.\n---\n\nSee [ref](./REFERENCE.md).\n"
    )
    ref = SkillRef(
        name="mine",
        path=directory / "SKILL.md",
        source_kind="git",
        git_repo="owner/name",
        git_sha="a" * 40,
    )

    snap = snapshot_skill(ref)

    assert snap.source_kind == "git"
    assert snap.git_sha == "a" * 40
    assert set(snap.files) == {"SKILL.md", "REFERENCE.md"}


def test_a_git_sources_provenance_comes_from_the_ref_not_the_checkout(tmp_path: Path):
    """Caliper resolved the commit at fetch, so the checkout is never re-read.

    The checkout here sits in a repo at a *different* commit; the snapshot must
    still report what the ref says it fetched.
    """
    repo = tmp_path / "checkout"
    repo.mkdir()
    _git("init", "-b", "main", cwd=repo)
    _git("config", "user.email", "t@example.com", cwd=repo)
    _git("config", "user.name", "Test", cwd=repo)
    _write_skill(repo, "mine")
    _git("add", "-A", cwd=repo)
    _git("commit", "-m", "first", cwd=repo)

    snap = snapshot_skill(
        SkillRef(
            name="mine",
            path=repo / "SKILL.md",
            source_kind="git",
            git_repo="owner/name",
            git_sha="b" * 40,
        )
    )

    assert (snap.git_repo, snap.git_sha) == ("owner/name", "b" * 40)


def test_a_path_source_is_interrogated_for_its_repo_and_commit(tmp_path: Path):
    """A path source promised nothing, so its provenance is read off the disk."""
    repo = tmp_path / "work"
    repo.mkdir()
    _git("init", "-b", "main", cwd=repo)
    _git("config", "user.email", "t@example.com", cwd=repo)
    _git("config", "user.name", "Test", cwd=repo)
    local = _write_skill(repo / "skills" / "mine", "mine")
    _git("add", "-A", cwd=repo)
    _git("commit", "-m", "first", cwd=repo)

    snap = snapshot_skill(SkillRef(name="mine", path=local))

    assert snap.source_kind == "path"
    assert Path(snap.git_repo or "").resolve() == repo.resolve()
    assert snap.git_sha == _git("rev-parse", "HEAD", cwd=repo)
    assert set(snap.files) == {"SKILL.md"}


def test_a_missing_skill_file_snapshots_as_empty(tmp_path: Path):
    snap = snapshot_skill(SkillRef(name="gone", path=tmp_path / "gone" / "SKILL.md"))

    assert snap.files == {}
    assert snap.name == "gone"

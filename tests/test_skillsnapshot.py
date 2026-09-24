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


def test_a_reference_outside_the_skill_directory_is_not_captured(tmp_path: Path):
    """A reference outside the skill directory must not crash the run.

    Only the skill directory is installed, so the run never saw that file and
    the snapshot leaves it out. See docs/CONTEXT.md → Progressive disclosure.
    """
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "style.md").write_text("# Shared style guide\n")
    directory = tmp_path / "mine"
    directory.mkdir()
    (directory / "SKILL.md").write_text(
        "---\nname: mine\ndescription: d.\n---\n\nRead `../shared/style.md` first.\n"
    )

    snap = snapshot_skill(SkillRef(name="mine", path=directory / "SKILL.md"))

    assert set(snap.files) == {"SKILL.md"}


def test_an_external_symlink_is_not_captured(tmp_path: Path):
    """A skipped external symlink contributes no content to the snapshot."""
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "guide.md").write_text("# Shared guide v1\n")
    directory = tmp_path / "mine"
    directory.mkdir()
    (directory / "SKILL.md").write_text(
        "---\nname: mine\ndescription: d.\n---\n\nRead [guide](./guide.md).\n"
    )
    (directory / "guide.md").symlink_to(Path("../shared/guide.md"))

    snap = snapshot_skill(SkillRef(name="mine", path=directory / "SKILL.md"))

    assert set(snap.files) == {"SKILL.md"}


def test_a_changed_symlink_target_shows_as_drift(tmp_path: Path):
    """Editing an internal symlink's target shows up as drift."""
    directory = tmp_path / "mine"
    directory.mkdir()
    (directory / "real-guide.md").write_text("# Shared guide v1\n")
    (directory / "SKILL.md").write_text(
        "---\nname: mine\ndescription: d.\n---\n\nRead [guide](./guide.md).\n"
    )
    (directory / "guide.md").symlink_to(Path("real-guide.md"))
    ref = SkillRef(name="mine", path=directory / "SKILL.md")

    before = snapshot_skill(ref)
    (directory / "real-guide.md").write_text("# Shared guide v2\n")
    after = snapshot_skill(ref)

    assert before.files["guide.md"].hash != after.files["guide.md"].hash


def test_a_symlink_inside_the_directory_keeps_its_link_name(tmp_path: Path):
    """A symlink inside the directory is keyed by its own name, not its target's."""
    directory = tmp_path / "mine"
    directory.mkdir()
    (directory / "REAL.md").write_text("# Real\n")
    (directory / "SKILL.md").write_text(
        "---\nname: mine\ndescription: d.\n---\n\nSee [alias](./alias.md).\n"
    )
    (directory / "alias.md").symlink_to(Path("REAL.md"))

    snap = snapshot_skill(SkillRef(name="mine", path=directory / "SKILL.md"))

    assert set(snap.files) == {"SKILL.md", "alias.md"}
    assert snap.files["alias.md"].content == "# Real\n"


def test_a_reference_below_a_directory_symlink_is_not_snapshotted(tmp_path: Path):
    """Files under a symlinked directory are not snapshotted.

    ``install_skills`` uses ``rglob``, which does not follow directory
    symlinks, so those files were never installed. Tracking them would report
    drift for a change the agent never saw.
    """
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "style.md").write_text("# Shared style v1\n")
    directory = tmp_path / "mine"
    directory.mkdir()
    (directory / "SKILL.md").write_text(
        "---\nname: mine\ndescription: d.\n---\n\nRead ./references/style.md.\n"
    )
    (directory / "references").symlink_to(Path("../shared"), target_is_directory=True)
    ref = SkillRef(name="mine", path=directory / "SKILL.md")

    snap = snapshot_skill(ref)

    assert set(snap.files) == {"SKILL.md"}

    (shared / "style.md").write_text("# Shared style v2\n")

    assert snapshot_skill(ref).files == snap.files


def test_an_absolute_reference_outside_the_skill_directory_is_skipped(tmp_path: Path):
    """The exact case from issue #103: an absolute path to a shared guide.

    The relative-path test above hits the same branch, but the reported crash
    used an absolute path, so the repro is kept as its own test.
    """
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "style.md").write_text("shared style guide\n")
    directory = tmp_path / "mine"
    directory.mkdir()
    (directory / "SKILL.md").write_text(
        f"---\nname: mine\ndescription: d.\n---\n\nRead `{shared / 'style.md'}`.\n"
    )

    snap = snapshot_skill(SkillRef(name="mine", path=directory / "SKILL.md"))

    assert set(snap.files) == {"SKILL.md"}


def test_a_home_anchored_reference_outside_the_skill_directory_is_skipped(
    tmp_path: Path, monkeypatch
):
    """A `~/...` reference is expanded to an absolute path and then skipped.

    This takes a different code path than `../`, so it gets its own test.
    """
    home = tmp_path / "home"
    (home / ".claude" / "reference").mkdir(parents=True)
    (home / ".claude" / "reference" / "style.md").write_text("shared style guide\n")
    monkeypatch.setenv("HOME", str(home))
    directory = tmp_path / "mine"
    directory.mkdir()
    (directory / "SKILL.md").write_text(
        "---\nname: mine\ndescription: d.\n---\n\n"
        "Read `~/.claude/reference/style.md`.\n"
    )

    snap = snapshot_skill(SkillRef(name="mine", path=directory / "SKILL.md"))

    assert set(snap.files) == {"SKILL.md"}


def test_a_skill_reached_through_a_linked_directory_keeps_its_references(
    tmp_path: Path,
):
    """A skill behind a symlinked directory keeps references written via the link.

    ``install_skills`` copies from the directory as the spec named it, so the
    companions install normally. Checking references against the resolved
    directory instead would drop a path written with the symlink's name.
    """
    real = tmp_path / "real" / "mine"
    real.mkdir(parents=True)
    (real / "guide.md").write_text("# Guide\n")
    linked = tmp_path / "linked"
    linked.symlink_to(tmp_path / "real", target_is_directory=True)
    (real / "SKILL.md").write_text(
        f"---\nname: mine\ndescription: d.\n---\n\n"
        f"Read `{linked / 'mine' / 'guide.md'}`.\n"
    )

    snap = snapshot_skill(SkillRef(name="mine", path=linked / "mine" / "SKILL.md"))

    assert set(snap.files) == {"SKILL.md", "guide.md"}
    assert snap.files["guide.md"].content == "# Guide\n"


def test_a_companion_symlinked_to_the_skill_file_keeps_its_own_key(tmp_path: Path):
    """An `alias.md -> SKILL.md` symlink is snapshotted as its own file.

    ``shutil.copy2`` follows the link, so the alias is installed as a second
    file. Skipping it because it resolves to SKILL.md would hide any later
    change to it.
    """
    directory = tmp_path / "mine"
    directory.mkdir()
    (directory / "SKILL.md").write_text(
        "---\nname: mine\ndescription: d.\n---\n\nSee [alias](./alias.md).\n"
    )
    (directory / "alias.md").symlink_to(Path("SKILL.md"))

    snap = snapshot_skill(SkillRef(name="mine", path=directory / "SKILL.md"))

    assert set(snap.files) == {"SKILL.md", "alias.md"}


def test_a_linked_skill_keeps_a_reference_written_in_resolved_terms(tmp_path: Path):
    """A skill behind a symlinked directory also keeps references via the real path.

    The companion is installed at the same relative path whichever spelling the
    SKILL.md uses, so both spellings must be accepted.
    """
    real = tmp_path / "real" / "mine"
    real.mkdir(parents=True)
    (real / "guide.md").write_text("# Guide\n")
    linked = tmp_path / "linked"
    linked.symlink_to(tmp_path / "real", target_is_directory=True)
    (real / "SKILL.md").write_text(
        f"---\nname: mine\ndescription: d.\n---\n\nRead `{real / 'guide.md'}`.\n"
    )

    snap = snapshot_skill(SkillRef(name="mine", path=linked / "mine" / "SKILL.md"))

    assert set(snap.files) == {"SKILL.md", "guide.md"}
    assert snap.files["guide.md"].content == "# Guide\n"

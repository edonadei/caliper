"""Snapshotting a neighbourhood member — files captured, provenance recorded.

The files captured are the files the install copies, so most tests here assert
the exact set of keys a skill tree produces.

See docs/CONTEXT.md → Skill drift and
docs/adr/0017-unpinned-git-sources-are-allowed-because-drift-is-reported.md.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from caliper.schema.results import SkillSnapshot
from caliper.skills import SkillRef, install_skills
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


def _mine(tmp_path: Path) -> Path:
    """A skill directory ``mine`` whose SKILL.md names nothing else."""
    directory = tmp_path / "mine"
    _write_skill(directory, "mine")
    return directory


def test_every_installed_file_is_captured_whether_or_not_skill_md_names_it(
    tmp_path: Path,
):
    """A companion SKILL.md never names was still installed, so the agent saw it.

    A script referenced only from REFERENCE.md, a data fixture, a plain-text
    reference: editing any of them changes what the agent could read.
    """
    directory = _mine(tmp_path)
    (directory / "REFERENCE.md").write_text("Run `scripts/run.sh`.\n")
    (directory / "scripts").mkdir()
    (directory / "scripts" / "run.sh").write_text("echo hi\n")
    (directory / "fixtures").mkdir()
    (directory / "fixtures" / "data.json").write_text('{"a": 1}\n')
    (directory / "references").mkdir()
    (directory / "references" / "x.txt").write_text("notes\n")

    snap = snapshot_skill(SkillRef(name="mine", path=directory / "SKILL.md"))

    assert set(snap.files) == {
        "SKILL.md",
        "REFERENCE.md",
        "scripts/run.sh",
        "fixtures/data.json",
        "references/x.txt",
    }
    assert snap.files["scripts/run.sh"].content == "echo hi\n"


def test_editing_an_unreferenced_file_changes_the_digest(tmp_path: Path):
    directory = _mine(tmp_path)
    (directory / "fixtures").mkdir()
    (directory / "fixtures" / "data.json").write_text('{"a": 1}\n')
    ref = SkillRef(name="mine", path=directory / "SKILL.md")

    before = snapshot_skill(ref)
    (directory / "fixtures" / "data.json").write_text('{"a": 2}\n')
    after = snapshot_skill(ref)

    assert before.content_digest != after.content_digest


def test_a_line_ending_change_is_drift(tmp_path: Path):
    """The install copies bytes, so CRLF and LF are different files to the agent."""
    directory = _mine(tmp_path)
    (directory / "REFERENCE.md").write_bytes(b"one\r\ntwo\r\n")
    ref = SkillRef(name="mine", path=directory / "SKILL.md")

    before = snapshot_skill(ref)
    (directory / "REFERENCE.md").write_bytes(b"one\ntwo\n")
    after = snapshot_skill(ref)

    assert before.content_digest != after.content_digest


def test_cheat_surfaces_and_forbidden_files_are_not_captured(tmp_path: Path):
    """The install skips them for every member, so the run never saw them."""
    directory = _mine(tmp_path)
    (directory / ".git").mkdir()
    (directory / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (directory / "mine.eval.yaml").write_text("tasks: []\n")
    (directory / ".caliper").mkdir()
    (directory / ".caliper" / "run.json").write_text("{}\n")
    (directory / "answers").mkdir()
    (directory / "answers" / "key.md").write_text("the answer is 42\n")
    (directory / "hint.md").symlink_to(Path("answers/key.md"))
    (directory / "config.md").symlink_to(Path(".git/HEAD"))

    snap = snapshot_skill(
        SkillRef(name="mine", path=directory / "SKILL.md"), ["answers/"]
    )

    assert set(snap.files) == {"SKILL.md"}


def test_a_file_below_a_directory_symlink_is_not_captured(tmp_path: Path):
    """Nothing below a directory symlink is installed, so nothing is captured.

    ``rglob`` does not descend into directory symlinks. Tracking those files
    would report drift for a change the agent never saw.
    """
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "style.md").write_text("# Shared style v1\n")
    directory = _mine(tmp_path)
    (directory / "references").symlink_to(Path("../shared"), target_is_directory=True)
    ref = SkillRef(name="mine", path=directory / "SKILL.md")

    snap = snapshot_skill(ref)

    assert set(snap.files) == {"SKILL.md"}

    (shared / "style.md").write_text("# Shared style v2\n")

    assert snapshot_skill(ref).content_digest == snap.content_digest


def test_a_file_outside_the_skill_directory_is_not_captured(tmp_path: Path):
    """Naming a shared guide is not installing it.

    See docs/CONTEXT.md → Progressive disclosure.
    """
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "style.md").write_text("# Shared style guide\n")
    directory = tmp_path / "mine"
    _write_skill(
        directory,
        "mine",
        "---\nname: mine\ndescription: d.\n---\n\nRead `../shared/style.md`.\n",
    )

    snap = snapshot_skill(SkillRef(name="mine", path=directory / "SKILL.md"))

    assert set(snap.files) == {"SKILL.md"}


def test_a_file_symlink_is_captured_under_its_link_name_with_its_targets_bytes(
    tmp_path: Path,
):
    """The agent read the target's bytes at the link's path.

    ``shutil.copy2`` follows the link, even when the target lives outside the
    directory, so a later edit to that target is drift.
    """
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "guide.md").write_text("# Shared guide v1\n")
    directory = _mine(tmp_path)
    (directory / "guide.md").symlink_to(Path("../shared/guide.md"))
    (directory / "alias.md").symlink_to(Path("SKILL.md"))
    ref = SkillRef(name="mine", path=directory / "SKILL.md")

    before = snapshot_skill(ref)
    (shared / "guide.md").write_text("# Shared guide v2\n")
    after = snapshot_skill(ref)

    assert set(before.files) == {"SKILL.md", "alias.md", "guide.md"}
    assert before.files["guide.md"].content == "# Shared guide v1\n"
    assert before.files["alias.md"] == before.files["SKILL.md"]
    assert before.content_digest != after.content_digest


def test_a_skill_reached_through_a_linked_directory_captures_the_same_files(
    tmp_path: Path,
):
    real = tmp_path / "real" / "mine"
    _write_skill(real, "mine")
    (real / "references").mkdir()
    (real / "references" / "guide.md").write_text("# Guide\n")
    linked = tmp_path / "linked"
    linked.symlink_to(tmp_path / "real", target_is_directory=True)

    through_link = snapshot_skill(
        SkillRef(name="mine", path=linked / "mine" / "SKILL.md")
    )
    direct = snapshot_skill(SkillRef(name="mine", path=real / "SKILL.md"))

    assert set(through_link.files) == {"SKILL.md", "references/guide.md"}
    assert through_link.files == direct.files


def test_a_binary_file_is_captured_by_its_bytes_without_text(tmp_path: Path):
    directory = _mine(tmp_path)
    (directory / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe")

    snap = snapshot_skill(SkillRef(name="mine", path=directory / "SKILL.md"))

    assert snap.files["logo.png"].content is None
    assert snap.files["logo.png"].hash == (
        "sha256:608b46bb11fb3fd7be889e6e75fb4deee0ea15be13ad778fef9d04007828b877"
    )
    assert SkillSnapshot.model_validate_json(snap.model_dump_json()) == snap


def test_a_file_too_large_to_install_is_not_captured(tmp_path: Path):
    directory = _mine(tmp_path)
    (directory / "blob.bin").write_bytes(b"x" * (6 * 1024 * 1024))

    snap = snapshot_skill(SkillRef(name="mine", path=directory / "SKILL.md"))

    assert set(snap.files) == {"SKILL.md"}


def test_the_snapshot_holds_exactly_the_files_the_install_wrote(tmp_path: Path):
    """The invariant drift rests on: what was hashed is what the agent got."""
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "guide.md").write_text("# Shared guide\n")
    directory = _mine(tmp_path)
    (directory / "REFERENCE.md").write_text("# Reference\n")
    (directory / "scripts").mkdir()
    (directory / "scripts" / "run.sh").write_text("echo hi\n")
    (directory / "fixtures").mkdir()
    (directory / "fixtures" / "data.json").write_text('{"a": 1}\n')
    (directory / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe")
    (directory / "blob.bin").write_bytes(b"x" * (6 * 1024 * 1024))
    (directory / ".git").mkdir()
    (directory / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (directory / "mine.eval.yaml").write_text("tasks: []\n")
    (directory / ".caliper").mkdir()
    (directory / ".caliper" / "run.json").write_text("{}\n")
    (directory / "answers").mkdir()
    (directory / "answers" / "key.md").write_text("the answer is 42\n")
    (directory / "hint.md").symlink_to(Path("answers/key.md"))
    (directory / "guide.md").symlink_to(Path("../shared/guide.md"))
    (directory / "references").symlink_to(Path("../shared"), target_is_directory=True)
    ref = SkillRef(name="mine", path=directory / "SKILL.md")
    root = tmp_path / "root"

    snap = snapshot_skill(ref, ["answers/"])
    install_skills([ref], root, ["answers/"])

    installed = {
        item.relative_to(root / "mine").as_posix()
        for item in (root / "mine").rglob("*")
        if item.is_file()
    }
    assert installed == {
        "SKILL.md",
        "REFERENCE.md",
        "scripts/run.sh",
        "fixtures/data.json",
        "logo.png",
        "guide.md",
    }
    assert set(snap.files) == installed

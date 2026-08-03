from pathlib import Path

import pytest

from caliper.skills import (
    SkillResolutionError,
    frontmatter_name,
    install_skills,
    resolve_skills,
)


def write_skill(directory: Path, name: str, body: str = "Do the thing.") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "SKILL.md"
    path.write_text(f"---\nname: {name}\ndescription: Test skill.\n---\n\n{body}\n")
    return path


# --- frontmatter_name -----------------------------------------------------


def test_frontmatter_name_reads_the_name_key():
    assert frontmatter_name("---\nname: grill-skill\ndesc: x\n---\n\nbody") == (
        "grill-skill"
    )


def test_frontmatter_name_is_none_without_frontmatter():
    assert frontmatter_name("# Just a heading\n") is None


def test_frontmatter_name_is_none_when_name_key_absent():
    assert frontmatter_name("---\ndescription: no name here\n---\n") is None


def test_frontmatter_name_strips_quotes():
    assert frontmatter_name('---\nname: "quoted-skill"\n---\n') == "quoted-skill"


# --- resolve_skills -------------------------------------------------------


def test_resolves_relative_paths_against_the_spec_dir(tmp_path):
    write_skill(tmp_path / "mine", "mine")
    refs = resolve_skills(["./mine/SKILL.md"], tmp_path)
    assert [r.name for r in refs] == ["mine"]
    assert refs[0].path == (tmp_path / "mine" / "SKILL.md").resolve()
    assert refs[0].directory == (tmp_path / "mine").resolve()


def test_resolves_several_entries_as_peers_in_order(tmp_path):
    write_skill(tmp_path / "a", "alpha")
    write_skill(tmp_path / "b", "beta")
    refs = resolve_skills(["./a/SKILL.md", "./b/SKILL.md"], tmp_path)
    assert [r.name for r in refs] == ["alpha", "beta"]


def test_empty_list_resolves_to_no_skills(tmp_path):
    assert resolve_skills([], tmp_path) == []


def test_missing_file_is_an_error(tmp_path):
    with pytest.raises(SkillResolutionError, match="does not exist"):
        resolve_skills(["./nope/SKILL.md"], tmp_path)


def test_lone_slash_command_file_is_rejected(tmp_path):
    (tmp_path / "review.md").write_text("Review the code.\n")
    with pytest.raises(SkillResolutionError) as exc:
        resolve_skills(["./review.md"], tmp_path)
    # The error has to teach the new model, not just say "no".
    assert "SKILL.md" in str(exc.value)
    assert "install" in str(exc.value).lower()


def test_skill_without_frontmatter_name_is_rejected(tmp_path):
    (tmp_path / "nameless").mkdir()
    (tmp_path / "nameless" / "SKILL.md").write_text("---\ndescription: x\n---\nbody")
    with pytest.raises(SkillResolutionError, match="frontmatter"):
        resolve_skills(["./nameless/SKILL.md"], tmp_path)


def test_duplicate_names_collide_on_one_install_path_and_are_rejected(tmp_path):
    write_skill(tmp_path / "one", "same-name")
    write_skill(tmp_path / "two", "same-name")
    with pytest.raises(SkillResolutionError, match="same-name"):
        resolve_skills(["./one/SKILL.md", "./two/SKILL.md"], tmp_path)


@pytest.mark.parametrize("bad", ["../escape", "with/slash", ".", ""])
def test_name_must_be_a_safe_directory_component(tmp_path, bad):
    (tmp_path / "s").mkdir()
    (tmp_path / "s" / "SKILL.md").write_text(f"---\nname: {bad!r}\n---\nbody")
    with pytest.raises(SkillResolutionError):
        resolve_skills(["./s/SKILL.md"], tmp_path)


# --- install_skills -------------------------------------------------------


def test_installs_at_skills_root_under_the_frontmatter_name(tmp_path):
    write_skill(tmp_path / "src", "unit-normalizer")
    refs = resolve_skills(["./src/SKILL.md"], tmp_path)
    root = tmp_path / "root"

    install_skills(refs, root, [])

    installed = root / "unit-normalizer" / "SKILL.md"
    assert installed.exists()
    assert "unit-normalizer" in installed.read_text()


def test_install_carries_the_whole_directory_for_progressive_disclosure(tmp_path):
    write_skill(tmp_path / "src", "deep")
    (tmp_path / "src" / "REFERENCE.md").write_text("the details")
    (tmp_path / "src" / "references").mkdir()
    (tmp_path / "src" / "references" / "extra.md").write_text("more details")
    refs = resolve_skills(["./src/SKILL.md"], tmp_path)
    root = tmp_path / "root"

    install_skills(refs, root, [])

    assert (root / "deep" / "REFERENCE.md").read_text() == "the details"
    assert (root / "deep" / "references" / "extra.md").read_text() == "more details"


def test_install_excludes_cheat_surfaces_for_every_entry(tmp_path):
    # A neighbour's answer key is as much an answer key as the subject's.
    for dirname, skill in (("subject", "subject"), ("neighbour", "neighbour")):
        write_skill(tmp_path / dirname, skill)
        (tmp_path / dirname / f"{skill}.eval.yaml").write_text("tasks: []")
        (tmp_path / dirname / ".caliper").mkdir()
        (tmp_path / dirname / ".caliper" / "run.json").write_text("{}")
        (tmp_path / dirname / ".git").mkdir()
        (tmp_path / dirname / ".git" / "HEAD").write_text("ref: refs/heads/main")

    refs = resolve_skills(["./subject/SKILL.md", "./neighbour/SKILL.md"], tmp_path)
    root = tmp_path / "root"

    install_skills(refs, root, [])

    for skill in ("subject", "neighbour"):
        assert (root / skill / "SKILL.md").exists()
        assert not (root / skill / f"{skill}.eval.yaml").exists()
        assert not (root / skill / ".caliper").exists()
        assert not (root / skill / ".git").exists()


def test_install_honours_sandbox_forbidden_files(tmp_path):
    write_skill(tmp_path / "src", "guarded")
    (tmp_path / "src" / "answers.txt").write_text("the answer is 42")
    refs = resolve_skills(["./src/SKILL.md"], tmp_path)
    root = tmp_path / "root"

    install_skills(refs, root, [r".*answers\.txt$"])

    assert (root / "guarded" / "SKILL.md").exists()
    assert not (root / "guarded" / "answers.txt").exists()


def test_install_skips_oversized_files(tmp_path):
    write_skill(tmp_path / "src", "big")
    (tmp_path / "src" / "blob.bin").write_bytes(b"x" * (6 * 1024 * 1024))
    refs = resolve_skills(["./src/SKILL.md"], tmp_path)
    root = tmp_path / "root"

    install_skills(refs, root, [])

    assert not (root / "big" / "blob.bin").exists()


def test_install_of_nothing_creates_no_root(tmp_path):
    root = tmp_path / "root"
    install_skills([], root, [])
    assert not root.exists()

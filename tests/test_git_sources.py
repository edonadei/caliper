"""Git sources in ``skills:`` — schema, fetch, resolution and drift.

See docs/adr/0016-caliper-fetches-git-sources-itself.md,
docs/adr/0017-unpinned-git-sources-are-allowed-because-drift-is-reported.md
and docs/CONTEXT.md → Skill source, Skill drift.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from caliper.main import app
from caliper.schema.spec import EvalSpec, GitSkillSource
from caliper.skillfetch import SkillFetchError, SkillFetcher
from caliper.skills import SkillResolutionError, resolve_skills


SKILL_BODY = """---
name: {name}
description: A skill for testing.
---

# {name}

Body.
"""


def _spec(skills: list) -> EvalSpec:
    return EvalSpec.model_validate(
        {
            "skills": skills,
            "tasks": [{"name": "t", "prompt": "p", "expect": "e"}],
        }
    )


def _write_skill(directory: Path, name: str, body: str | None = None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "SKILL.md"
    path.write_text(body if body is not None else SKILL_BODY.format(name=name))
    return path


def _git(*args: str, cwd: Path) -> str:
    return subprocess.check_output(
        ["git", *args],
        cwd=str(cwd),
        text=True,
        stderr=subprocess.DEVNULL,
    ).strip()


@pytest.fixture
def origin(tmp_path: Path) -> Path:
    """A real local git repo standing in for a remote, with one skill at root."""
    repo = tmp_path / "origin"
    repo.mkdir()
    _git("init", "-b", "main", cwd=repo)
    _git("config", "user.email", "t@example.com", cwd=repo)
    _git("config", "user.name", "Test", cwd=repo)
    _write_skill(repo, "tdd")
    _write_skill(repo / "skills" / "pr-review", "pr-review")
    _git("add", "-A", cwd=repo)
    _git("commit", "-m", "first", cwd=repo)
    return repo


def _head(repo: Path) -> str:
    return _git("rev-parse", "HEAD", cwd=repo)


# ── schema ───────────────────────────────────────────────────────────────────


def test_bare_string_entry_stays_a_path_source():
    spec = _spec(["./SKILL.md"])
    assert spec.skills == ["./SKILL.md"]


def test_mapping_entry_parses_as_a_git_source():
    spec = _spec([{"repo": "owner/name", "ref": "a1b2c3d", "path": "s/SKILL.md"}])
    entry = spec.skills[0]
    assert isinstance(entry, GitSkillSource)
    assert (entry.repo, entry.ref, entry.path) == (
        "owner/name",
        "a1b2c3d",
        "s/SKILL.md",
    )


def test_ref_and_path_are_optional():
    entry = _spec([{"repo": "owner/name"}]).skills[0]
    assert entry.ref is None
    assert entry.path == "SKILL.md"


def test_path_must_point_at_a_skill_md():
    with pytest.raises(ValidationError, match="SKILL.md"):
        _spec([{"repo": "owner/name", "path": "skills/tdd"}])


def test_path_may_not_escape_the_repo():
    with pytest.raises(ValidationError, match="inside the repo"):
        _spec([{"repo": "owner/name", "path": "../../etc/SKILL.md"}])


def test_repo_is_required():
    with pytest.raises(ValidationError):
        _spec([{"ref": "main"}])


def test_unknown_key_on_a_git_source_is_refused():
    with pytest.raises(ValidationError):
        _spec([{"repo": "owner/name", "branch": "main"}])


def test_mixed_entries_are_allowed():
    spec = _spec(["./SKILL.md", {"repo": "owner/name"}])
    assert isinstance(spec.skills[0], str)
    assert isinstance(spec.skills[1], GitSkillSource)


# ── fetching ─────────────────────────────────────────────────────────────────


def test_fetch_resolves_a_branch_to_a_commit(tmp_path: Path, origin: Path):
    fetcher = SkillFetcher(cache_dir=tmp_path / "cache")
    fetched = fetcher.materialize(GitSkillSource(repo=str(origin), ref="main"))
    assert fetched is not None
    assert fetched.sha == _head(origin)
    assert fetched.path.read_text().startswith("---")


def test_fetch_honours_the_path_field(tmp_path: Path, origin: Path):
    fetcher = SkillFetcher(cache_dir=tmp_path / "cache")
    fetched = fetcher.materialize(
        GitSkillSource(repo=str(origin), path="skills/pr-review/SKILL.md")
    )
    assert fetched is not None
    assert "name: pr-review" in fetched.path.read_text()


def test_a_pinned_sha_is_served_from_cache_without_network(
    tmp_path: Path, origin: Path
):
    sha = _head(origin)
    cache = tmp_path / "cache"
    src = GitSkillSource(repo=str(origin), ref=sha)
    SkillFetcher(cache_dir=cache).materialize(src)

    # Make the origin unreachable: a warm pinned entry must not touch it.
    origin.rename(tmp_path / "origin-gone")
    fetched = SkillFetcher(cache_dir=cache).materialize(src)
    assert fetched is not None
    assert fetched.sha == sha


def test_an_unreachable_uncached_source_refuses(tmp_path: Path):
    fetcher = SkillFetcher(cache_dir=tmp_path / "cache")
    with pytest.raises(SkillFetchError, match="could not be fetched"):
        fetcher.materialize(GitSkillSource(repo=str(tmp_path / "nope"), ref="main"))


def test_a_fetch_failure_reaches_the_caller_as_a_resolution_error(tmp_path: Path):
    """One exception type for "the neighbourhood is unusable", either source."""
    with pytest.raises(SkillResolutionError, match="could not be fetched"):
        resolve_skills(
            [GitSkillSource(repo=str(tmp_path / "nope"), ref="main")],
            tmp_path,
            fetcher=SkillFetcher(cache_dir=tmp_path / "cache"),
        )


def test_an_unreachable_but_cached_ref_warns_and_uses_the_cache(
    tmp_path: Path, origin: Path
):
    cache = tmp_path / "cache"
    src = GitSkillSource(repo=str(origin), ref="main")
    sha = SkillFetcher(cache_dir=cache).materialize(src).sha

    origin.rename(tmp_path / "origin-gone")
    fetcher = SkillFetcher(cache_dir=cache)
    fetched = fetcher.materialize(src)

    assert fetched.sha == sha
    assert any("cached" in w for w in fetcher.warnings)


def test_offline_mode_never_touches_the_network(tmp_path: Path, origin: Path):
    fetcher = SkillFetcher(cache_dir=tmp_path / "cache", offline=True)
    assert fetcher.materialize(GitSkillSource(repo=str(origin), ref="main")) is None
    assert fetcher.unresolved


def test_offline_mode_does_not_fetch_an_uncached_pinned_sha(tmp_path: Path):
    """A commit-shaped ref must not sneak past the offline gate into a clone."""
    fetcher = SkillFetcher(cache_dir=tmp_path / "cache", offline=True)
    src = GitSkillSource(repo="https://example.invalid/nope", ref="a" * 40)

    assert fetcher.materialize(src) is None
    assert fetcher.unresolved


def test_a_hex_named_branch_is_resolved_not_assumed_to_be_a_commit(
    tmp_path: Path, origin: Path
):
    """`ref: abcdef1` may be a branch; asking the remote is what tells them apart."""
    _git("branch", "abcdef1", cwd=origin)

    fetched = SkillFetcher(cache_dir=tmp_path / "cache").materialize(
        GitSkillSource(repo=str(origin), ref="abcdef1")
    )

    assert fetched is not None
    assert fetched.sha == _head(origin)


def test_a_warning_is_pushed_out_as_it_happens(tmp_path: Path, origin: Path):
    """Collected-and-printed-later loses the notice on runs that then fail."""
    cache = tmp_path / "cache"
    src = GitSkillSource(repo=str(origin), ref="main")
    SkillFetcher(cache_dir=cache).materialize(src)
    origin.rename(tmp_path / "origin-gone")

    seen: list[str] = []
    fetcher = SkillFetcher(cache_dir=cache, on_warning=seen.append)
    fetcher.materialize(src)

    assert seen == fetcher.warnings
    assert seen


def test_offline_mode_still_serves_a_warm_cache(tmp_path: Path, origin: Path):
    cache = tmp_path / "cache"
    src = GitSkillSource(repo=str(origin), ref=_head(origin))
    SkillFetcher(cache_dir=cache).materialize(src)

    fetcher = SkillFetcher(cache_dir=cache, offline=True)
    assert fetcher.materialize(src) is not None
    assert not fetcher.unresolved


def test_a_missing_skill_md_inside_the_repo_refuses(tmp_path: Path, origin: Path):
    fetcher = SkillFetcher(cache_dir=tmp_path / "cache")
    with pytest.raises(SkillFetchError, match="does not contain"):
        fetcher.materialize(
            GitSkillSource(repo=str(origin), path="skills/absent/SKILL.md")
        )


def test_owner_name_shorthand_expands_to_a_github_url():
    """git reads `owner/name` as a relative path, so caliper has to expand it."""
    assert (
        SkillFetcher.clone_url("vercel-labs/agent-skills")
        == "https://github.com/vercel-labs/agent-skills"
    )


@pytest.mark.parametrize(
    "repo",
    [
        "https://github.com/owner/name",
        "git@github.com:owner/name.git",
        "/abs/path/to/repo",
        "./relative/repo",
        "~/repo",
        "owner/name/extra",
    ],
)
def test_only_bare_owner_name_is_treated_as_shorthand(repo: str):
    """Anything git already understands must pass through untouched."""
    assert SkillFetcher.clone_url(repo) == repo


def test_a_local_repo_is_still_reachable_by_relative_path(tmp_path: Path, origin: Path):
    """`./owner/name` is the escape hatch from shorthand expansion."""
    fetched = SkillFetcher(cache_dir=tmp_path / "cache").materialize(
        GitSkillSource(repo=f"./{origin.relative_to(Path.cwd())}", ref="main")
        if origin.is_relative_to(Path.cwd())
        else GitSkillSource(repo=str(origin), ref="main")
    )
    assert fetched is not None


def test_the_cache_key_and_messages_keep_the_spec_s_own_string(tmp_path: Path):
    """A spec that wrote `owner/name` is never reported back under a URL."""
    fetcher = SkillFetcher(cache_dir=tmp_path / "cache")
    src = GitSkillSource(repo="owner/name", ref="main")
    assert fetcher._label(src) == "owner/name@main"
    assert "owner-name" in fetcher._repo_dir(src.repo).name


# ── resolution ───────────────────────────────────────────────────────────────


def test_resolve_records_provenance_for_both_kinds(tmp_path: Path, origin: Path):
    local = _write_skill(tmp_path / "mine", "mine")
    fetcher = SkillFetcher(cache_dir=tmp_path / "cache")

    refs = resolve_skills(
        [str(local), GitSkillSource(repo=str(origin), ref="main")],
        tmp_path,
        fetcher=fetcher,
    )

    assert [r.name for r in refs] == ["mine", "tdd"]
    assert refs[0].source_kind == "path"
    assert refs[0].git_sha is None
    assert refs[1].source_kind == "git"
    assert refs[1].git_sha == _head(origin)
    assert refs[1].git_repo == str(origin)


def test_a_skill_referencing_companion_files_still_snapshots(tmp_path: Path):
    """Progressive disclosure is the normal shape, so the snapshot must survive it.

    Every other snapshot test uses a SKILL.md that points at nothing, which is
    what let a name collision in the referenced-file loop reach a real run.
    """
    from caliper.runner import _SkillSnapshotter
    from caliper.skills import SkillRef

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

    snap = _SkillSnapshotter().snapshot(ref)

    assert snap.source_kind == "git"
    assert snap.git_sha == "a" * 40
    assert set(snap.files) == {"SKILL.md", "REFERENCE.md"}


def test_a_git_source_colliding_on_name_is_refused(tmp_path: Path, origin: Path):
    local = _write_skill(tmp_path / "mine", "tdd")
    fetcher = SkillFetcher(cache_dir=tmp_path / "cache")

    with pytest.raises(SkillResolutionError, match="both declare name"):
        resolve_skills(
            [str(local), GitSkillSource(repo=str(origin), ref="main")],
            tmp_path,
            fetcher=fetcher,
        )


def test_validate_stays_offline_with_an_uncached_git_source(
    tmp_path: Path, origin: Path, monkeypatch
):
    """A schema check must not become a connectivity check."""
    monkeypatch.setenv("CALIPER_CACHE_DIR", str(tmp_path / "cache"))
    spec = tmp_path / "demo.eval.yaml"
    spec.write_text(
        f"skills:\n"
        f"  - repo: {origin}\n"
        f"    ref: main\n"
        f"tasks:\n"
        f"  - name: t\n"
        f"    prompt: p\n"
        f"    activates: [tdd]\n"
    )

    result = CliRunner().invoke(app, ["validate", str(spec)])

    assert result.exit_code == 0, result.output
    # Never "bare agent": the neighbourhood is unknown here, not empty. And the
    # activates: closure check stands down rather than refusing a name it simply
    # could not see.
    assert "not cached" in result.output
    assert "bare agent" not in result.output


def test_run_refuses_an_unfetchable_git_source(tmp_path: Path, monkeypatch):
    """A member silently absent would measure against competition that wasn't there."""
    monkeypatch.setenv("CALIPER_CACHE_DIR", str(tmp_path / "cache"))
    spec = tmp_path / "demo.eval.yaml"
    spec.write_text(
        f"skills:\n"
        f"  - repo: {tmp_path / 'nope'}\n"
        f"    ref: main\n"
        f"tasks:\n"
        f"  - name: t\n"
        f"    prompt: p\n"
        f"    expect: something\n"
    )

    result = CliRunner().invoke(app, ["run", str(spec), "--k", "1"])

    # Exit code only: the refusal panel is printed inside rich's live Progress,
    # which CliRunner's non-tty capture drops. The message itself is asserted at
    # the resolve seam, where it is not behind a live display.
    assert result.exit_code == 1


def test_offline_resolution_skips_an_uncached_git_source(tmp_path: Path, origin: Path):
    local = _write_skill(tmp_path / "mine", "mine")
    fetcher = SkillFetcher(cache_dir=tmp_path / "cache", offline=True)

    refs = resolve_skills(
        [str(local), GitSkillSource(repo=str(origin), ref="main")],
        tmp_path,
        fetcher=fetcher,
    )

    assert [r.name for r in refs] == ["mine"]
    assert fetcher.unresolved

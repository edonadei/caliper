"""Materializing a [[skill source|git source]] into a local directory.

Caliper fetches git sources itself rather than shelling out to a skill package
manager: the managers' lockfiles record the *ref string you typed* rather than a
commit, and never verify on restore, so adopting one would reimport the silent
drift this exists to remove. See
docs/adr/0016-caliper-fetches-git-sources-itself.md.

The cache is content-addressed by **resolved commit**, so a checkout is
immutable once written and shared across every spec that names it. A pinned
entry therefore costs no network after its first fetch; an unpinned one costs
one ``ls-remote`` per run to learn whether the cache is current — which is the
incentive to pin, in place of a rule requiring it (docs/adr/0017).
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from caliper.schema.spec import GitSkillSource

# A ref that is already a commit needs no resolution, so a pinned entry can be
# served from a warm cache with the network unreachable.
_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
# Keeps a cache directory readable (`agent-skills-1f4a…`) without letting a repo
# string containing slashes or `..` decide where we write.
_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")

_GIT_TIMEOUT = 120


class SkillFetchError(ValueError):
    """A git source that cannot be materialized at all.

    Wrapped into ``SkillResolutionError`` at the ``resolve_skills`` seam, so a
    caller handles one exception type for "the neighbourhood is unusable"
    however the entry was written.
    """


@dataclass(frozen=True)
class FetchedSkill:
    """Where a git source landed, and the commit it landed at."""

    path: Path
    sha: str
    repo: str


def default_cache_dir() -> Path:
    """``$CALIPER_CACHE_DIR`` or the XDG cache location.

    Never inside the repo: a checkout keyed by commit is shared across specs and
    would otherwise be a large accidental commit waiting to happen.
    """
    override = os.environ.get("CALIPER_CACHE_DIR")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".cache"
    return base / "caliper"


@dataclass
class SkillFetcher:
    """Clones git sources into a commit-addressed cache.

    ``offline`` is what lets ``validate`` stay usable without a network: an
    uncached source resolves to ``None`` and is recorded in :attr:`unresolved`
    instead of raising, so a schema check never becomes a connectivity check.
    """

    cache_dir: Path | None = None
    offline: bool = False
    warnings: list[str] = field(default_factory=list)
    # Sources skipped because we are offline and they are not cached. Only ever
    # populated in offline mode — a run refuses instead.
    unresolved: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.cache_dir = Path(self.cache_dir or default_cache_dir() / "skills")

    # ── public API ───────────────────────────────────────────────────────────

    def materialize(self, src: GitSkillSource) -> FetchedSkill | None:
        """Check out ``src`` and return where its SKILL.md landed.

        Returns ``None`` only in offline mode with a cold cache. Every other
        failure raises: a run with a member silently absent would measure
        activation against competition that was not there and report a
        plausible number with nothing inviting suspicion — the same reasoning
        ``apply_ablation`` refuses an unknown ``--ablate`` name on.
        """
        sha, stale = self._resolve(src)
        if sha is None:
            self.unresolved.append(self._label(src))
            return None

        checkout = self._checkout_dir(src.repo, sha)
        if not (checkout / ".git").exists():
            self._clone(src, sha, checkout)

        skill_md = checkout / src.path
        if not skill_md.is_file():
            raise SkillFetchError(
                f"{self._label(src)} does not contain '{src.path}' at {sha[:7]}.\n\n"
                "A git source names one SKILL.md inside the repo; `path:` "
                "defaults to a SKILL.md at the repo root. Check the path, or "
                "the ref if the skill moved."
            )

        if stale:
            self.warnings.append(
                f"{self._label(src)}: cannot reach the remote to resolve "
                f"'{src.ref or 'the default branch'}' — using cached {sha[:7]}"
            )
        return FetchedSkill(path=skill_md, sha=sha, repo=src.repo)

    # ── resolution ───────────────────────────────────────────────────────────

    def _resolve(self, src: GitSkillSource) -> tuple[str | None, bool]:
        """(commit, served-from-a-stale-cache) for this source.

        A commit-shaped ref is taken at face value, which is what keeps a pinned
        entry offline-clean. Anything else is a moving target and has to be
        re-resolved every run.
        """
        if src.ref and _SHA_RE.match(src.ref):
            return self._expand_short_sha(src), False

        if self.offline:
            remembered = self._remembered(src)
            return (remembered, remembered is not None)

        try:
            return self._ls_remote(src), False
        except SkillFetchError as exc:
            # The remote is unreachable. A cached commit for this ref is fully
            # auditable — it lands in the run's snapshot and `compare` reports
            # the drift — so serving it beats blocking the local loop for a
            # network reason unrelated to what is being measured (ADR 0017).
            remembered = self._remembered(src)
            if remembered is not None:
                return remembered, True
            raise SkillFetchError(
                f"{self._label(src)} could not be fetched and is not cached.\n\n"
                f"  {exc}\n\n"
                "Caliper refuses rather than running without it: a member "
                "silently absent from the neighbourhood would measure "
                "activation against competition that was not there."
            ) from exc

    def _expand_short_sha(self, src: GitSkillSource) -> str:
        """The cached commit this pinned ref abbreviates, or the ref itself.

        Expanding from the cache is what lets ``ref: a1b2c3d`` hit a warm
        checkout without a network round trip; an uncached abbreviation is
        handed to ``git fetch`` to accept or reject.
        """
        assert src.ref is not None
        repo_dir = self._repo_dir(src.repo)
        if (repo_dir / src.ref / ".git").exists():
            return src.ref
        if repo_dir.is_dir():
            for child in sorted(repo_dir.iterdir()):
                if child.name.startswith(src.ref) and (child / ".git").exists():
                    return child.name
        return src.ref

    def _ls_remote(self, src: GitSkillSource) -> str:
        ref = src.ref or "HEAD"
        out = self._git("ls-remote", src.repo, ref)
        for line in out.splitlines():
            sha, _, name = line.partition("\t")
            if name.strip() in (ref, f"refs/heads/{ref}", f"refs/tags/{ref}"):
                self._remember(src, sha.strip())
                return sha.strip()
        if src.ref:
            raise SkillFetchError(
                f"{self._label(src)}: the remote has no ref '{src.ref}'.\n\n"
                "Check the branch, tag or commit — or drop `ref:` to track the "
                "default branch."
            )
        raise SkillFetchError(f"{self._label(src)}: the remote reported no HEAD")

    def _clone(self, src: GitSkillSource, sha: str, dest: Path) -> None:
        """Depth-1 checkout of exactly ``sha``.

        ``init`` + ``fetch <sha>`` rather than ``clone --branch``: one code path
        serves a branch, a tag and a commit, and all three stay shallow.
        """
        dest.mkdir(parents=True, exist_ok=True)
        try:
            self._git("init", "-q", cwd=dest)
            self._git("remote", "add", "origin", src.repo, cwd=dest)
            self._git("fetch", "-q", "--depth", "1", "origin", sha, cwd=dest)
            self._git("checkout", "-q", "FETCH_HEAD", cwd=dest)
        except SkillFetchError:
            # A half-written checkout would be indistinguishable from a good one
            # on the next run, since the cache key is the commit.
            _rmtree(dest)
            raise

    # ── the ref → commit memo ────────────────────────────────────────────────
    #
    # One file per repo mapping the ref *string* to the commit it last resolved
    # to. Not a lockfile and never consulted when the remote is reachable: its
    # only job is to name a cached checkout when `ls-remote` cannot run.

    def _memo_path(self, repo: str) -> Path:
        return self._repo_dir(repo) / "refs.txt"

    def _remember(self, src: GitSkillSource, sha: str) -> None:
        memo = self._memo_path(src.repo)
        entries = self._read_memo(memo)
        entries[src.ref or "HEAD"] = sha
        memo.parent.mkdir(parents=True, exist_ok=True)
        memo.write_text("".join(f"{k}\t{v}\n" for k, v in sorted(entries.items())))

    def _remembered(self, src: GitSkillSource) -> str | None:
        sha = self._read_memo(self._memo_path(src.repo)).get(src.ref or "HEAD")
        if sha and (self._checkout_dir(src.repo, sha) / ".git").exists():
            return sha
        return None

    @staticmethod
    def _read_memo(memo: Path) -> dict[str, str]:
        try:
            text = memo.read_text()
        except OSError:
            return {}
        out: dict[str, str] = {}
        for line in text.splitlines():
            ref, _, sha = line.partition("\t")
            if ref and sha:
                out[ref] = sha
        return out

    # ── paths and plumbing ───────────────────────────────────────────────────

    def _repo_dir(self, repo: str) -> Path:
        digest = hashlib.sha256(repo.encode()).hexdigest()[:12]
        slug = _SLUG_RE.sub("-", repo).strip("-")[-40:] or "repo"
        assert self.cache_dir is not None
        return self.cache_dir / f"{slug}-{digest}"

    def _checkout_dir(self, repo: str, sha: str) -> Path:
        return self._repo_dir(repo) / sha

    @staticmethod
    def _label(src: GitSkillSource) -> str:
        at = f"@{src.ref}" if src.ref else ""
        return f"{src.repo}{at}"

    @staticmethod
    def _git(*args: str, cwd: Path | None = None) -> str:
        try:
            proc = subprocess.run(
                ["git", *args],
                cwd=str(cwd) if cwd else None,
                capture_output=True,
                text=True,
                timeout=_GIT_TIMEOUT,
            )
        except FileNotFoundError:
            raise SkillFetchError(
                "git is not installed, and a spec declaring a git source needs "
                "it to fetch the skill."
            )
        except subprocess.TimeoutExpired:
            raise SkillFetchError(f"git {args[0]} timed out after {_GIT_TIMEOUT}s")
        if proc.returncode != 0:
            # The *first* line: git puts the diagnosis there and follows it with
            # remediation prose, so the last line of "repository not found /
            # make sure you have the correct access rights / and the repository
            # exists" is the half that says nothing.
            detail = (proc.stderr or proc.stdout or "").strip().splitlines()
            raise SkillFetchError(
                f"git {args[0]} failed: {detail[0] if detail else 'unknown error'}"
            )
        return proc.stdout


def _rmtree(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)

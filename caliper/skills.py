"""Resolving and installing the [[skill neighbourhood]].

Caliper's one loading discipline is **install-and-discover**: every declared
skill is copied to the backend's native skills root as
``<skills_root>/<name>/SKILL.md`` and nothing is ever preloaded into the agent's
context. This module owns the two halves that are the same on every backend —
turning spec paths into named ``SkillRef``s, and copying a skill directory into
a root — while each harness supplies only the root itself.

Identity is the frontmatter ``name:``, and caliper *establishes* it by
installing at a directory of that name rather than discovering it afterwards, so
a backend reports back exactly the name the spec wrote. See
docs/adr/0013-install-and-discover-is-the-only-loading-discipline.md and
docs/CONTEXT.md → Install-and-discover.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from caliper.sandbox import SpecSandbox
from caliper.schema.results import MCP_ABLATION_PREFIX
from caliper.schema.spec import GitSkillSource, McpServer
from caliper.skillfetch import SkillFetchError, SkillFetcher

# ``--ablate`` names its subject bare when only one kind declares it, and by
# kind — ``skill:`` / ``mcp:`` — when both do. A server's qualifier is the same
# ``mcp:`` the run's marker records, so what you type is what the saved run
# shows. Neither a skill's frontmatter name nor an ``mcp:`` key may contain ``:``
# (see the regexes that validate them), so a qualified entry can never be
# mistaken for a declared name.
_SKILL_QUALIFIER = "skill:"

# Directories never installed: results (cheat surface), VCS, caches.
_EXCLUDE_DIRS = {".caliper", ".git", "__pycache__", "node_modules", ".venv"}
# Per-file cap so a stray large fixture or binary can't bloat every attempt.
_MAX_FILE_BYTES = 5 * 1024 * 1024

_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---", re.DOTALL)
_NAME_RE = re.compile(r"^name:\s*(.+?)\s*$", re.MULTILINE)
# A name becomes a directory component, so it must not traverse or nest.
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class SkillResolutionError(ValueError):
    """A declared skill cannot be installed, with guidance on why.

    ``title`` is the run seam's bordered-panel heading; a subclass covering a
    different subject axis names its own.
    """

    title: str | None = None


class AblationError(SkillResolutionError):
    """An ``--ablate`` subject that cannot be resolved, with guidance on why.

    A bad ``--ablate`` name is not a malformed ``skills:`` entry, so the panel
    names the axis that actually failed.
    """

    title = "Invalid ablation"


@dataclass(frozen=True)
class SkillRef:
    """One member of the neighbourhood: its identity and where it lives.

    ``source_kind`` records *how the entry was written*, not what role the skill
    plays — peers stay peers. It travels into the run's snapshot because it is
    what grades [[skill drift]]: a git source made a reproducibility claim the
    spec could keep, a path source made none. See docs/adr/0017 and
    docs/CONTEXT.md → Skill source.
    """

    name: str
    path: Path
    source_kind: str = "path"
    git_repo: str | None = None
    git_sha: str | None = None

    @property
    def directory(self) -> Path:
        return self.path.parent


def frontmatter_name(text: str) -> str | None:
    """The ``name:`` from a SKILL.md's YAML frontmatter, or ``None``."""
    block = _FRONTMATTER_RE.match(text)
    if not block:
        return None
    match = _NAME_RE.search(block.group(1))
    if not match:
        return None
    return match.group(1).strip().strip("\"'")


def resolve_skills(
    entries: list[str | GitSkillSource],
    spec_dir: Path,
    *,
    fetcher: SkillFetcher | None = None,
) -> list[SkillRef]:
    """Turn a spec's ``skills:`` sources into named refs, or explain the refusal.

    A bare string is a **path source**: relative paths resolve against the spec's
    own directory. A mapping is a **git source**, materialized through
    ``fetcher`` into a commit-addressed cache. Either way every entry must end at
    a ``SKILL.md`` carrying a frontmatter ``name:``, because the name is the
    install directory and therefore the identity everything downstream matches
    on. Names must be unique across the whole neighbourhood, whatever the source:
    two entries with one name would collide on a single install path, silently
    installing one over the other.

    An offline ``fetcher`` (what ``validate`` passes) **skips** an uncached git
    source rather than raising, recording it on ``fetcher.unresolved`` — a
    schema check should not become a connectivity check.
    """
    fetcher = fetcher or SkillFetcher()
    refs: list[SkillRef] = []
    seen: dict[str, Path] = {}

    for entry in entries:
        if isinstance(entry, GitSkillSource):
            try:
                fetched = fetcher.materialize(entry)
            except SkillFetchError as exc:
                raise SkillResolutionError(str(exc)) from exc
            if fetched is None:
                continue
            path, raw = fetched.path, f"{entry.repo}:{entry.path}"
            escaping = _links_escaping(path, fetched.checkout)
            if escaping:
                listed = "\n".join(f"  {link}" for link in escaping)
                raise SkillResolutionError(
                    f"{raw} at {fetched.sha[:7]} has symlinks that point "
                    f"outside the repo:\n{listed}\n\n"
                    "Their bytes would come from whichever machine runs the "
                    "eval, so the pinned commit would no longer say what was "
                    "installed. Keep shared files inside the repo and link to "
                    "them there. See docs/adr/0027."
                )
            provenance = {
                "source_kind": "git",
                "git_repo": fetched.repo,
                "git_sha": fetched.sha,
            }
        else:
            raw = entry
            path = Path(entry).expanduser()
            if not path.is_absolute():
                path = spec_dir / path
            path = path.resolve()
            provenance = {"source_kind": "path"}

        if path.name != "SKILL.md":
            raise SkillResolutionError(
                f"'{raw}' is not a SKILL.md.\n"
                "Caliper installs each declared skill at "
                "<skills_root>/<name>/SKILL.md and lets the agent discover it, "
                "so a skill needs a directory and a frontmatter name:. A lone "
                "slash-command .md has neither — and is invoked by a human "
                "typing /name, which caliper's single-shot harness never does.\n"
                "Wrap it in a directory as SKILL.md with name: and description: "
                "frontmatter."
            )
        if not path.exists():
            raise SkillResolutionError(f"skill '{raw}' does not exist: {path}")

        name = frontmatter_name(path.read_text())
        if not name:
            raise SkillResolutionError(
                f"{path} has no frontmatter name:.\n"
                "The name is the skill's identity: caliper installs at "
                "<skills_root>/<name>/ so the backend reports back exactly the "
                "name the spec wrote. Without it nothing can match an "
                "activation to a skills: entry."
            )
        if not _SAFE_NAME_RE.match(name) or name in (".", ".."):
            raise SkillResolutionError(
                f"{path} has an unusable frontmatter name: {name!r}.\n"
                "The name becomes a directory component, so it must contain "
                "only letters, digits, dot, dash or underscore."
            )
        if name in seen:
            raise SkillResolutionError(
                f"two skills both declare name: {name!r} "
                f"({seen[name]} and {path}).\n"
                "They would collide on one install path. Rename one."
            )

        seen[name] = path
        refs.append(SkillRef(name=name, path=path, **provenance))

    return refs


@dataclass(frozen=True)
class Ablation:
    """What ``--ablate`` removed, resolved against the spec's declared subjects.

    ``skill_refs`` and ``mcp_servers`` are the declared sets minus the removal —
    what the run actually installs and hands the backend. ``names`` is the run's
    own record of the subjects removed, bare for a skill and ``mcp:`` qualified
    for a server, so a saved run stays self-describing. ``skill_names`` is the
    removed-skill subset alone: it is what the activation expectation hangs on,
    because removing a *server* says nothing about whether a skill fires.
    """

    skill_refs: list[SkillRef]
    mcp_servers: dict[str, McpServer]
    names: list[str]
    skill_names: list[str]


def apply_ablation(
    refs: list[SkillRef],
    ablate: list[str],
    mcp_servers: dict[str, McpServer] | None = None,
) -> Ablation:
    """The declared subjects minus the named ones, or an explanation of the refusal.

    ``--ablate`` resolves against the union of the spec's ``skills:`` and its
    ``mcp:`` servers: a name only one kind declares removes that one, and an
    ablated server is left out of the harness config for the run. Subjecthood is
    a *runtime axis*: the spec keeps a list of peers and the invocation names
    which one is being removed, exactly as the engine is chosen per invocation
    rather than authored into the file, and nothing about that reasoning is
    specific to skills. See
    docs/adr/0015-ablation-names-its-subject-at-the-invocation.md,
    docs/adr/0025-ablation-covers-mcp-servers.md and docs/CONTEXT.md → Ablation.

    An undeclared name is refused rather than ignored: it would otherwise
    produce a full run recorded and labelled as an ablation, which is a
    plausible-looking number with nothing in the output to invite suspicion. A
    name **both** kinds declare is refused too, and the fix is the same shape —
    a qualifier says which subject was meant, because guessing would remove the
    wrong one and still report a plausible number.

    Duplicates are collapsed in the marker: `--ablate x --ablate mcp:x` removes
    one subject, and the run records one removal.
    """
    servers = dict(mcp_servers or {})
    if not ablate:
        return Ablation(
            skill_refs=list(refs),
            mcp_servers=servers,
            names=[],
            skill_names=[],
        )

    declared_skills = {ref.name for ref in refs}
    declared_servers = set(servers)
    removed_skills: set[str] = set()
    removed_servers: set[str] = set()
    unknown: list[str] = []
    ambiguous: list[str] = []

    for entry in ablate:
        kind, name = _subject_of(entry)
        in_skills = name in declared_skills
        in_servers = name in declared_servers

        if kind == "skill":
            if in_skills:
                removed_skills.add(name)
            else:
                unknown.append(entry)
        elif kind == "mcp":
            if in_servers:
                removed_servers.add(name)
            else:
                unknown.append(entry)
        elif in_skills and in_servers:
            ambiguous.append(name)
        elif in_skills:
            removed_skills.add(name)
        elif in_servers:
            removed_servers.add(name)
        else:
            # A bare name, or one carrying a qualifier we do not recognize. It
            # names nothing either way.
            unknown.append(entry)

    if unknown:
        raise AblationError(
            _unknown_ablation_message(unknown, declared_skills, declared_servers)
        )
    if ambiguous:
        raise AblationError(_ambiguous_ablation_message(ambiguous))

    return Ablation(
        skill_refs=[ref for ref in refs if ref.name not in removed_skills],
        mcp_servers={
            name: server
            for name, server in servers.items()
            if name not in removed_servers
        },
        names=sorted(
            [
                *removed_skills,
                *(f"{MCP_ABLATION_PREFIX}{name}" for name in removed_servers),
            ]
        ),
        skill_names=sorted(removed_skills),
    )


def _subject_of(entry: str) -> tuple[str | None, str]:
    """``(kind, name)`` for an ``--ablate`` entry, ``kind`` ``None`` when bare.

    An unrecognized ``prefix:`` reads as a bare name: it cannot match a declared
    subject (neither a skill name nor an ``mcp:`` key may contain ``:``), so it
    lands in the unknown bucket with the entry the user actually typed.
    """
    if entry.startswith(_SKILL_QUALIFIER):
        return "skill", entry[len(_SKILL_QUALIFIER) :]
    if entry.startswith(MCP_ABLATION_PREFIX):
        return "mcp", entry[len(MCP_ABLATION_PREFIX) :]
    return None, entry


def _unknown_ablation_message(
    unknown: list[str], declared_skills: set[str], declared_servers: set[str]
) -> str:
    skills = ", ".join(sorted(declared_skills)) or "(none)"
    servers = ", ".join(sorted(declared_servers)) or "(none)"
    return (
        f"--ablate names {', '.join(unknown)}, which the spec declares neither as "
        f"a skill nor as an mcp: server (skills: {skills}; mcp: {servers}).\n\n"
        "Ablation removes a *declared* member of the run, so an unrecognised name "
        "would leave everything installed while the run recorded itself as an "
        "ablation. Correct the name: a skill's identity is its frontmatter name:, "
        "a server's is its key under mcp:."
    )


def _ambiguous_ablation_message(ambiguous: list[str]) -> str:
    qualified = " and ".join(
        f"{_SKILL_QUALIFIER}{name}/{MCP_ABLATION_PREFIX}{name}" for name in ambiguous
    )
    return (
        f"--ablate names {', '.join(ambiguous)}, which the spec declares both as "
        "a skill and as an mcp: server.\n\n"
        "Refusing to guess which one you meant: removing the wrong one still "
        f"produces a plausible number. Qualify it — {qualified}."
    )


def validate_activates(
    tasks: list,
    refs: list[SkillRef],
    *,
    spec_label: str = "spec",
    closed: bool = True,
) -> None:
    """Refuse an ``activates:`` naming a skill the spec never declared.

    The neighbourhood is closed: an undeclared skill is not installed and so can
    *never* activate, which would make the expectation unsatisfiable — a task
    stuck at 0% for a reason no transcript explains. Shared by ``validate`` (so
    it is caught before you pay for anything) and the run seam (so it is caught
    even when ``validate`` was skipped).

    ``closed=False`` stands the check down, for the one case where the check's
    premise does not hold: an offline ``validate`` could not see every declared
    member, so ``refs`` is a subset of the neighbourhood rather than all of it,
    and an unrecognised name means "not visible from here" rather than "not
    declared". A run never passes this — it has fetched everything or refused.
    """
    if not closed:
        return
    declared = {ref.name for ref in refs}
    for task in tasks:
        unknown = [name for name in (task.activates or []) if name not in declared]
        if not unknown:
            continue
        listed = ", ".join(sorted(declared)) or "(none)"
        raise SkillResolutionError(
            f"Task '{task.name}' expects {', '.join(unknown)} to activate, but "
            f"the {spec_label}'s skills: declares only {listed}.\n\n"
            "An undeclared skill is never installed, so it cannot activate and "
            "the expectation could never be met. Add it to skills:, or correct "
            "the name (identity is the frontmatter name:, not the filename)."
        )


def install_skills(
    refs: list[SkillRef], skills_root: Path, forbidden_files: list[str]
) -> None:
    """Install each ref's directory at ``skills_root/<name>/``.

    The whole directory travels, so a skill's relative pointers
    (``[REFERENCE.md](REFERENCE.md)``, ``references/``) resolve exactly as they
    would from a real install — which is what keeps progressive disclosure
    measurable.

    Cheat surfaces are never installed, and the exclusions apply to **every**
    ref: a neighbour's ``.eval.yaml`` is as much an answer key as the subject's.
    Which paths those are is the sandbox's rule, not this module's — it is asked
    rather than re-derived (docs/CONTEXT.md → Sandbox).
    """
    sandbox = SpecSandbox(declared=list(forbidden_files))

    for ref in refs:
        dest = skills_root / ref.name
        for item in sorted(ref.directory.rglob("*")):
            if not item.is_file():
                continue
            rel = item.relative_to(ref.directory)
            if not installs(ref.directory, rel, sandbox):
                continue
            try:
                if item.stat().st_size > _MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)


def installs(directory: Path, rel: Path, sandbox: SpecSandbox) -> bool:
    """Whether the file at ``directory / rel`` passes the install exclusions.

    A file symlink is judged twice, by its own path and by its target's. The
    link is followed on install (docs/adr/0027), so an ``alias.md`` pointing at
    ``answers/key.md`` or ``.git/config`` would otherwise deliver an excluded
    file under an innocent name. A target inside the skill directory is matched
    by its path relative to it, like any other file; one outside it (a shared
    guide) by its absolute path, which is how ``forbidden_files`` matches paths
    in a transcript too.

    Size is left to the caller: it is a limit on copying, not an exclusion.
    """
    paths = [rel]
    item = directory / rel
    if item.is_symlink():
        target = item.resolve()
        root = directory.resolve()
        paths.append(
            target.relative_to(root) if target.is_relative_to(root) else target
        )
    return all(_passes_exclusions(path, sandbox) for path in paths)


def _passes_exclusions(path: Path, sandbox: SpecSandbox) -> bool:
    if any(part in _EXCLUDE_DIRS for part in path.parts):
        return False
    if path.name.endswith(".eval.yaml"):
        return False
    return sandbox.permits_install(path.as_posix())


def _links_escaping(skill_md: Path, checkout: Path) -> list[str]:
    """Symlinks a git source's install would follow out of its clone.

    Two places are checked. First the selected path itself: ``SKILL.md`` and
    every directory from the checkout down to it, because a directory link there
    would make the whole skill a host directory. Then each file link below the
    skill directory that the install would copy. Links the install never
    follows (under an excluded directory, or to a directory) contribute no
    bytes, so they are left alone. ``forbidden_files`` is not known here, so a
    forbidden outward link still refuses.

    Dangling links are checked too: one that dangles here can resolve on
    another machine. Listed relative to the checkout, for the refusal message.
    """
    root = checkout.resolve()

    def escapes(item: Path) -> bool:
        return item.is_symlink() and not item.resolve().is_relative_to(root)

    parts = skill_md.relative_to(checkout).parts
    selected = [checkout.joinpath(*parts[:i]) for i in range(1, len(parts) + 1)]
    escaping = [str(item.relative_to(checkout)) for item in selected if escapes(item)]
    if escaping:
        # The skill directory is (or sits in) a host directory; scanning it
        # would list the host's files.
        return escaping

    directory = skill_md.parent
    no_patterns = SpecSandbox()
    return [
        str(item.relative_to(checkout))
        for item in sorted(directory.rglob("*"))
        if escapes(item)
        and not item.is_dir()
        and _passes_exclusions(item.relative_to(directory), no_patterns)
    ]

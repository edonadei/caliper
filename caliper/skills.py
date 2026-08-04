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

# Directories never installed: results (cheat surface), VCS, caches.
_EXCLUDE_DIRS = {".caliper", ".git", "__pycache__", "node_modules", ".venv"}
# Per-file cap so a stray large fixture or binary can't bloat every attempt.
_MAX_FILE_BYTES = 5 * 1024 * 1024

_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---", re.DOTALL)
_NAME_RE = re.compile(r"^name:\s*(.+?)\s*$", re.MULTILINE)
# A name becomes a directory component, so it must not traverse or nest.
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class SkillResolutionError(ValueError):
    """A declared skill cannot be installed, with guidance on why."""


@dataclass(frozen=True)
class SkillRef:
    """One member of the neighbourhood: its identity and where it lives."""

    name: str
    path: Path

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


def resolve_skills(skill_paths: list[str], spec_dir: Path) -> list[SkillRef]:
    """Turn a spec's ``skills:`` paths into named refs, or explain the refusal.

    Relative paths resolve against the spec's own directory. Every entry must be
    a ``SKILL.md`` carrying a frontmatter ``name:``, because the name is the
    install directory and therefore the identity everything downstream matches
    on. Names must be unique: two entries with one name would collide on a single
    install path, silently installing one over the other.
    """
    refs: list[SkillRef] = []
    seen: dict[str, Path] = {}

    for raw in skill_paths:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = spec_dir / path
        path = path.resolve()

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
        refs.append(SkillRef(name=name, path=path))

    return refs


def apply_ablation(refs: list[SkillRef], ablate: list[str]) -> list[SkillRef]:
    """The neighbourhood minus the named skills, or an explanation of the refusal.

    Subjecthood is a *runtime axis*: the spec keeps a list of peers and the
    invocation names which one is being removed, exactly as the engine is chosen
    per invocation rather than authored into the file. See
    docs/adr/0015-ablation-names-its-subject-at-the-invocation.md
    and docs/CONTEXT.md → Ablation.

    An undeclared name is refused rather than ignored: it would otherwise
    produce a full run recorded and labelled as an ablation, which is a
    plausible-looking number with nothing in the output to invite suspicion.
    """
    if not ablate:
        return list(refs)

    declared = {ref.name for ref in refs}
    unknown = [name for name in ablate if name not in declared]
    if unknown:
        listed = ", ".join(sorted(declared)) or "(none)"
        raise SkillResolutionError(
            f"--ablate names {', '.join(unknown)}, which the spec's skills: does "
            f"not declare (it declares {listed}).\n\n"
            "Ablation removes a *declared* member of the neighbourhood, so an "
            "unknown name would leave every skill installed while the run "
            "recorded itself as an ablation. Correct the name (identity is the "
            "frontmatter name:, not the filename)."
        )
    return [ref for ref in refs if ref.name not in set(ablate)]


def validate_activates(
    tasks: list, refs: list[SkillRef], *, spec_label: str = "spec"
) -> None:
    """Refuse an ``activates:`` naming a skill the spec never declared.

    The neighbourhood is closed: an undeclared skill is not installed and so can
    *never* activate, which would make the expectation unsatisfiable — a task
    stuck at 0% for a reason no transcript explains. Shared by ``validate`` (so
    it is caught before you pay for anything) and the run seam (so it is caught
    even when ``validate`` was skipped).
    """
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
    """
    forbidden = [re.compile(p) for p in forbidden_files]

    for ref in refs:
        dest = skills_root / ref.name
        for item in sorted(ref.directory.rglob("*")):
            if not item.is_file():
                continue
            rel = item.relative_to(ref.directory)
            if any(part in _EXCLUDE_DIRS for part in rel.parts):
                continue
            if item.name.endswith(".eval.yaml"):
                continue
            rel_posix = rel.as_posix()
            if any(
                r.search(rel_posix) or r.search("./" + rel_posix) for r in forbidden
            ):
                continue
            try:
                if item.stat().st_size > _MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)

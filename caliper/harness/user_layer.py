"""The user layer an attempt loads beside the spec's own (docs/adr/0028).

One module stages it and reads it back. :func:`stage` copies this machine's
rules, skills and plugins into the attempt's isolated home and returns a
:class:`StagedUserLayer`, which answers the two questions a finished attempt
asks of it: what was loaded, and which exposed skills were the CLI's own. A
backend contributes only where it keeps that layer — ``user_rules``,
``user_settings_file``, how it names a skill (``user_skill_name``), what its
CLI ships (``bundled_skill_names``), and its plugins (``stage_plugins``) —
never the staging itself
(docs/adr/0020-a-backend-declares-its-chores-rather-than-performing-them.md).
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING

from caliper.harness.base import HarnessConfigurationError
from caliper.skills import SkillRef, install_skills

if TYPE_CHECKING:
    from caliper.harness.base import AgentReport, CliHarness, RunContext

# Recorded beside the staged user files when the backend cannot list its MCP
# servers: the inventory is partial, and says so (docs/adr/0028).
UNLISTED_MCP = "mcp:(not listed)"


@dataclass
class StagedUserLayer:
    """What one attempt was given from this machine's user layer.

    Empty, and ``loaded`` ``None``, when the attempt runs isolated.
    """

    loads_user_layer: bool = False
    # The names the spec declares, ablated ones included: a user's own skill
    # or server never takes one of them, and never reads as built-in.
    spec_skill_names: frozenset[str] = frozenset()
    spec_mcp_names: frozenset[str] = frozenset()
    # Kind-prefixed files: ``rules:``, ``settings:``, ``plugin:``.
    files: tuple[str, ...] = ()
    # The user's own skills, installed flat at the skills root.
    skills: tuple[str, ...] = ()
    # Namespaced plugin skills, by command name, to the path their reads match:
    # their file paths do not contain their names.
    plugin_skill_paths: dict[str, str] = field(default_factory=dict)

    @property
    def skill_names(self) -> list[str]:
        """Every skill the user layer brought, for activation to recognize."""
        return sorted(set(self.skills) | set(self.plugin_skill_paths))

    def loaded(self, report: AgentReport) -> list[str] | None:
        """What ``AttemptResult.loaded_user_customizations`` records.

        ``None`` when isolated. When the MCP inventory is unknown, or reading
        it failed (a provenance record must not sink the attempt, see
        :class:`AgentReport`), the staged files are still recorded, marked with
        :data:`UNLISTED_MCP` so the partial list never reads as complete. The
        inventory is read only here, so an isolated attempt never reads it.
        """
        if not self.loads_user_layer:
            return None
        staged = {*self.files, *(f"skill:{name}" for name in self.skill_names)}
        servers = report.mcp_servers
        if servers is None:
            return sorted({UNLISTED_MCP, *staged})
        return sorted(
            {f"mcp:{name}" for name in set(servers) - self.spec_mcp_names} | staged
        )

    def builtin(self, report: AgentReport) -> list[str] | None:
        """The exposed skills that are neither declared nor the user's own.

        ``None`` when the CLI does not list what it exposed (docs/CONTEXT.md →
        Built-in skill).
        """
        if report.exposed_skills is None:
            return None
        return sorted(
            set(report.exposed_skills) - self.spec_skill_names - set(self.skill_names)
        )


def stage(harness: CliHarness, ctx: RunContext) -> StagedUserLayer:
    """Copy the user layer into the attempt's home, if this attempt loads it.

    Runs after ``_prepare``: a backend's skills root can depend on state it
    sets up (hermes' ``HERMES_HOME``, pi's agent dir). Files are recorded before
    the agent runs, so it cannot mutate what the record says it was given.
    """
    # The run seam already turns the choice off on a backend without MCP
    # (environment.choose_user_customizations); checked once more here so no
    # other context can stage a layer that backend has nothing to load into.
    isolated = StagedUserLayer(
        spec_skill_names=ctx.spec_skill_names, spec_mcp_names=ctx.spec_mcp_names
    )
    if not (ctx.user_customizations and harness.supports_mcp):
        return isolated
    files = _copy_rules(harness, ctx)
    plugin_files, plugin_skill_paths = harness.stage_plugins(ctx)
    return replace(
        isolated,
        loads_user_layer=True,
        files=(*files, *plugin_files),
        skills=tuple(_install_skills(harness, ctx)),
        plugin_skill_paths=plugin_skill_paths,
    )


def _copy_rules(harness: CliHarness, ctx: RunContext) -> list[str]:
    """Copy the user's rules files; record the settings file the home already has."""
    target = harness.skills_root(ctx).parent
    source = _real(target, ctx)
    names = []
    for name in harness.user_rules:
        if (source / name).is_file():
            (target / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / name, target / name)
            names.append(f"rules:{name}")
    settings = harness.user_settings_file
    if settings and (source / settings).is_file():
        names.append(f"settings:{settings}")
    return names


def _install_skills(harness: CliHarness, ctx: RunContext) -> list[str]:
    """Install the user's own skills flat at the skills root; return their names."""
    root = harness.skills_root(ctx)
    source = _real(root, ctx)
    refs: list[SkillRef] = []
    # Follow user-installed directory symlinks, but install independent copies.
    visited: set[Path] = set()
    bundled = harness.bundled_skill_names(source)
    for directory, directories, files in os.walk(source, followlinks=True):
        resolved = Path(directory).resolve()
        if resolved in visited:
            directories.clear()
            continue
        visited.add(resolved)
        # Hidden directories hold the CLI's own state and system skills
        # (codex's `.system`), which it installs for itself.
        directories[:] = sorted(d for d in directories if not d.startswith("."))
        if "SKILL.md" not in files:
            continue
        directories.clear()
        path = Path(directory) / "SKILL.md"
        name = harness.user_skill_name(path)
        if not name or name in ctx.spec_skill_names or name in bundled:
            continue
        if name in {".", ".."} or "/" in name or "\\" in name:
            raise HarnessConfigurationError(f"Invalid user skill name: {name!r}")
        # Installed flat, so two same-named skills (e.g. in different hermes
        # categories) cannot both land; the first in walk order wins.
        if any(ref.name == name for ref in refs):
            continue
        refs.append(SkillRef(name, path))
    install_skills(refs, root, ctx.forbidden_files)
    return sorted(ref.name for ref in refs)


def _real(isolated: Path, ctx: RunContext) -> Path:
    """The user's own copy of a path inside the attempt's isolated home."""
    return Path.home() / isolated.relative_to(ctx.isolated_home)

"""The run environment: what every attempt of a run is given, resolved once.

A run hands each attempt the same installed skills, the same ``mcp:`` servers
and the same answer to "load this machine's user customizations?". Each of
those depends on the spec, the invocation (``--ablate``,
``--user-customizations``) and what the backend can do, and each used to be
worked out, or re-checked, in more than one place. :func:`resolve_environment`
works them out once, before any paid attempt, and :meth:`RunEnvironment.context`
is the only place an attempt's :class:`RunContext` is built. The run command
also asks :func:`choose_user_customizations` early, for its notice; the rule
itself lives only here.
See docs/CONTEXT.md → Run environment.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from caliper.harness.base import HarnessBackend, HarnessConfigurationError, RunContext
from caliper.harness.mcp import resolve_declared_paths
from caliper.schema.spec import (
    DEFAULT_USER_CUSTOMIZATIONS,
    EvalSpec,
    McpServer,
    TaskSpec,
)
from caliper.skillfetch import SkillFetcher
from caliper.skills import SkillRef, apply_ablation, resolve_skills, validate_activates
from caliper.workdir import AttemptWorkdir


@dataclass(frozen=True)
class UserCustomizations:
    """Whether attempts load this machine's user customizations, and who said so.

    ``source`` is what decided it (docs/adr/0028): the invocation's flag, the
    spec's ``user_customizations:``, or the default. ``ignored`` is a request to
    load them on a backend without MCP, which has nothing to load them into, so
    ``load`` is ``False``.
    """

    load: bool
    source: Literal["flag", "spec", "default"]
    ignored: bool = False

    @property
    def explicit(self) -> bool:
        """Whether a flag or the spec chose it, which decides how loudly to say so."""
        return self.source != "default"


def choose_user_customizations(
    flag: bool | None, spec: EvalSpec, harness: HarnessBackend
) -> UserCustomizations:
    """The flag, else the spec, else the default; off on a backend without MCP."""
    if flag is not None:
        wanted, source = flag, "flag"
    elif spec.user_customizations is not None:
        wanted, source = spec.user_customizations, "spec"
    else:
        wanted, source = DEFAULT_USER_CUSTOMIZATIONS, "default"
    if wanted and not harness.supports_mcp:
        return UserCustomizations(load=False, source=source, ignored=True)
    return UserCustomizations(load=wanted, source=source)


@dataclass(frozen=True)
class RunEnvironment:
    """What every attempt of one run is given. Built by :func:`resolve_environment`."""

    # The skills installed: the declared neighbourhood minus anything ablated.
    skill_refs: list[SkillRef]
    # What ``--ablate`` removed, as the run records it: bare for a skill,
    # ``mcp:`` qualified for a server.
    ablated: list[str]
    # The removed skills alone. Truthy drops every task's activation
    # expectation; removing a server does not (docs/adr/0025).
    ablated_skills: list[str]
    # The ``mcp:`` servers left after ablation, paths anchored to the spec.
    # ``None`` when the spec declared no ``mcp:`` block; an empty mapping is a
    # declared block whose servers were all ablated. Both isolate the attempt
    # to zero servers (docs/adr/0026-attempts-never-see-account-connectors.md).
    mcp_servers: dict[str, McpServer] | None
    # Whether attempts load this machine's user customizations: the choice
    # :func:`choose_user_customizations` made, already off on a backend without
    # MCP.
    user_customizations: bool
    # Every name the spec declares, ablated ones included, so a user's own
    # skill or server never takes one of them (docs/adr/0028).
    spec_skill_names: frozenset[str]
    spec_mcp_names: frozenset[str]
    extra_path: list[str]
    forbidden_files: list[str]
    timeout: int

    def context(
        self, task: TaskSpec, attempt: int, workdir: AttemptWorkdir
    ) -> RunContext:
        """One invocation's context.

        Build a fresh one per invocation: a retried attempt must not inherit
        what the failed invocation left behind (docs/adr/0023).
        """
        return RunContext(
            task_id=task.id,
            attempt=attempt,
            prompt=task.prompt,
            skill_refs=self.skill_refs,
            # None: the harness uses the model it was built with; the engine is
            # resolved once at the run seam (docs/adr/0004), not per spec.
            model=None,
            timeout=self.timeout,
            isolated_home=workdir.home,
            workdir=workdir.path,
            extra_path=self.extra_path,
            mcp_servers=self.mcp_servers,
            user_customizations=self.user_customizations,
            spec_mcp_names=self.spec_mcp_names,
            spec_skill_names=self.spec_skill_names,
            forbidden_files=self.forbidden_files,
        )

    def expected_activation(self, task: TaskSpec) -> list[str] | None:
        """What this run asserts the task should activate — ``None`` if a skill was ablated.

        An ablated **skill** run **drops** the expectation rather than filtering
        the removed skill out of it. Filtering would assert a claim the author
        never wrote, and it inverts the delegating case: remove a parent and its
        neighbours correctly stop firing, so scoring that as a miss would report
        the finding as a failure. The observation is still recorded; only the
        verdict is withheld, so the column renders skipped rather than 0%. See
        docs/adr/0015-ablation-names-its-subject-at-the-invocation.md.

        Ablating a *server* leaves the expectation alone: ``activates:`` names
        skills, and every one of them is still installed, so withholding their
        verdict would drop a measurement nothing removed. See
        docs/adr/0025-ablation-covers-mcp-servers.md.
        """
        return None if self.ablated_skills else task.activates


def resolve_environment(
    spec: EvalSpec,
    spec_path: Path,
    *,
    harness: HarnessBackend,
    ablate: list[str],
    user_customizations: bool | None,
    timeout: int,
    fetcher: SkillFetcher | None = None,
    on_warning: Callable[[str], None] | None = None,
) -> RunEnvironment:
    """Resolve what every attempt will be given, or refuse before any paid attempt.

    Raises the spec's own resolution errors for a bad ``skills:`` entry or
    ``--ablate`` name, and ``HarnessConfigurationError`` when the spec declares
    ``mcp:`` servers the backend cannot provide.
    """
    spec_dir = spec_path.resolve().parent

    # Resolve the neighbourhood up front: a bad entry (a lone .md, a missing
    # frontmatter name:, a duplicate) should fail before any paid attempt.
    declared_refs = resolve_skills(
        list(spec.skills), spec_path.parent, fetcher=fetcher or SkillFetcher()
    )
    # Validated against the *declared* set, not the installed one: under
    # --ablate an `activates:` naming the removed skill has its expectation
    # dropped, not violated (docs/adr/0015).
    validate_activates(spec.tasks, declared_refs)
    # `--ablate` names a declared subject — a skill or an mcp: server — and this
    # removes it. Duplicates collapse: `--ablate x --ablate x` removes one.
    ablation = apply_ablation(declared_refs, list(ablate), mcp_servers=spec.mcp)

    # The *surviving* mcp: servers are the agent's tool environment for the
    # eval. On a backend that cannot provide them every attempt would test
    # something other than what the spec claims, so refuse. Ablation resolves
    # first, so a spec whose servers were all ablated still runs: their absence
    # is then the user's choice, recorded in RunMeta.ablated.
    # This guard relaxes by itself as each backend flips ``supports_mcp``.
    if ablation.mcp_servers and not harness.supports_mcp:
        # A backend whose lack of MCP is permanent by design supplies its own
        # hint; the others get the generic "not yet" message.
        if harness.mcp_unsupported_hint:
            raise HarnessConfigurationError(
                f"This eval declares mcp: servers, but the '{harness.name}' "
                "backend does not support MCP.\n\n" + harness.mcp_unsupported_hint
            )
        raise HarnessConfigurationError(
            f"This eval declares mcp: servers, but the '{harness.name}' backend does "
            "not support MCP yet. Only the 'claude-code' backend implements mcp: "
            "in this release.\n\n"
            "Re-run with --model claude-code (the default engine), or remove the "
            "mcp: block from the spec."
        )

    # Not refused, unlike a declared mcp: block: recorded as off, and said only
    # when someone asked for it, or it would fire on every pi run
    # (docs/adr/0028).
    customizations = choose_user_customizations(user_customizations, spec, harness)
    if customizations.ignored and customizations.explicit and on_warning:
        on_warning(
            f"User customizations have no effect on the '{harness.name}' backend, "
            "which has no MCP support; the run records it as off."
        )

    return RunEnvironment(
        skill_refs=ablation.skill_refs,
        ablated=ablation.names,
        ablated_skills=ablation.skill_names,
        # Field presence, not truthiness: an authored `mcp: {}` parses to an
        # empty mapping but still declares the block, and must isolate.
        # Attempts run in fresh workdirs, so ./ and ../ server paths are
        # anchored to the spec here, before any backend writes its config.
        mcp_servers=(
            resolve_declared_paths(ablation.mcp_servers, spec_dir)
            if "mcp" in spec.model_fields_set
            else None
        ),
        user_customizations=customizations.load,
        spec_skill_names=frozenset(ref.name for ref in declared_refs),
        spec_mcp_names=frozenset(spec.mcp),
        extra_path=[str((spec_dir / p).resolve()) for p in spec.sandbox.extra_path],
        forbidden_files=list(spec.sandbox.forbidden_files),
        timeout=timeout,
    )

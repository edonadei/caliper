"""``caliper vet``: decide whether to install a skill you did not write.

One command, one trust report (docs/CONTEXT.md → Trust report): a static scan
of the skill's files, and, with ``--container``, a contained run of the
built-in probes with canaries planted and egress logged. The report is printed,
saved under the results root's ``trust/`` directory, and its verdict is the
exit code a CI gate reads: ``3`` when the skill is ``unsafe`` (or, with
``--fail-on review``, anything short of ``no findings``).

The probes run only inside a container. Running an untrusted skill on the host
gives it the user's environment, which is the very thing being vetted
(docs/adr/0027, docs/adr/0035), so without ``--container`` a vet scans and
says plainly that behaviour was not observed.
"""

from __future__ import annotations

import shutil
import tempfile
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape

from caliper import cancel
from caliper.commands.diagnosis import BadInput, CannotRun, ExitCode, fail
from caliper.harness import get_harness
from caliper.harness.base import HarnessConfigurationError
from caliper.judge import EvalJudge
from caliper.reporter import make_progress, print_trust_report, update_progress
from caliper.runner import AttemptEvent, RunAborted, run
from caliper.runstore import CALIPER_DIR, RunStore
from caliper.schema.results import Outcome, RunResults
from caliper.schema.spec import (
    DEFAULT_BACKEND,
    VALID_BACKENDS,
    GitSkillSource,
    parse_target,
)
from caliper.skillfetch import SkillFetcher
from caliper.skills import SkillRef, SkillResolutionError, resolve_skills
from caliper.skillsnapshot import snapshot_skill
from caliper.trust.container import contain
from caliper.trust.egress import valid_host_pattern
from caliper.trust.probes import probe_spec, skill_description, spec_yaml
from caliper.trust.report import TrustReport, decide, dynamic_result, limits
from caliper.trust.scan import scan_skill

console = Console()

#: Where trust reports are filed, under the results root's ``.caliper/``.
TRUST_DIR = "trust"


class FailOn(str, Enum):
    UNSAFE = "unsafe"
    REVIEW = "review"


def vet_cmd(
    source: str = typer.Argument(
        ...,
        help=(
            "The skill: a SKILL.md or its directory, or anything git can clone "
            "(owner/name, a URL) when no such path exists"
        ),
    ),
    ref: str | None = typer.Option(
        None, "--ref", help="Git branch, tag or commit (git sources only)"
    ),
    path: str = typer.Option(
        "SKILL.md",
        "--path",
        help="The SKILL.md inside the repository (git sources only)",
    ),
    container: str | None = typer.Option(
        None,
        "--container",
        metavar="IMAGE",
        help=(
            "Run the probes inside this container image. Without it, vet only "
            "scans the files."
        ),
    ),
    model: str | None = typer.Option(
        None, "--model", "-m", help="Backend/model the probes run on"
    ),
    k: int = typer.Option(1, "--k", help="Attempts per probe"),
    timeout: int = typer.Option(300, "--timeout", help="Seconds per attempt"),
    workers: int = typer.Option(3, "--workers", help="Attempts to run in parallel"),
    allow_host: list[str] | None = typer.Option(
        None,
        "--allow-host",
        metavar="HOST",
        help="A host the skill may reach during the probes (repeatable)",
    ),
    fail_on: FailOn = typer.Option(
        FailOn.UNSAFE,
        "--fail-on",
        help="Exit 3 on an unsafe verdict, or on anything short of no findings",
    ),
    output: Path | None = typer.Option(
        None, "--output", help="Also write the trust report JSON here"
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="List informational findings too"
    ),
) -> None:
    if k < 1:
        fail(BadInput(f"--k must be at least 1, got {k}."))
    if timeout < 1:
        fail(BadInput(f"--timeout must be at least 1 second, got {timeout}."))
    if workers < 1:
        fail(BadInput(f"--workers must be at least 1, got {workers}."))
    for host in allow_host or []:
        if not valid_host_pattern(host):
            fail(BadInput(f"--allow-host {host!r} is not a host name."))

    entry = _source_entry(source, ref, path)
    try:
        refs = resolve_skills([entry], Path.cwd(), fetcher=SkillFetcher())
    except SkillResolutionError as exc:
        fail(exc)
    skill = refs[0]

    static, scanned = scan_skill(skill.directory)
    snapshot = snapshot_skill(skill, [])

    dynamic = None
    if container:
        results, run_path = _probe(
            skill,
            entry,
            container=container,
            model=model,
            k=k,
            timeout=timeout,
            workers=workers,
            allow_hosts=list(allow_host or []),
        )
        dynamic = dynamic_result(results, run_path)

    verdict, reasons = decide(static, dynamic)
    report = TrustReport(
        skill=skill.name,
        source=_describe(entry),
        git_sha=skill.git_sha,
        digest=snapshot.content_digest,
        created=datetime.now(tz=timezone.utc),
        files_scanned=scanned,
        static=static,
        dynamic=dynamic,
        verdict=verdict,
        reasons=reasons,
        limits=limits(dynamic),
    )
    print_trust_report(report, verbose=verbose)
    saved = _save(report)
    console.print(f"[dim]Trust report saved to {saved}[/dim]")
    if output:
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(report.model_dump_json(indent=2))
        except OSError as exc:
            console.print(f"[yellow]Could not write --output {output}: {exc}[/yellow]")

    if verdict == "unsafe" or (fail_on is FailOn.REVIEW and verdict != "no findings"):
        raise typer.Exit(ExitCode.BAR_NOT_MET)


def _source_entry(source: str, ref: str | None, path: str) -> str | GitSkillSource:
    """A path source when ``source`` exists on disk, else a git source."""
    local = Path(source).expanduser()
    if local.exists():
        if ref is not None or path != "SKILL.md":
            fail(BadInput("--ref and --path apply to git sources only."))
        skill_md = local / "SKILL.md" if local.is_dir() else local
        return str(skill_md.resolve())
    try:
        return GitSkillSource(repo=source, ref=ref, path=path)
    except ValueError as exc:
        fail(BadInput(f"Invalid --path: {exc}"))


def _describe(entry: str | GitSkillSource) -> str:
    if isinstance(entry, GitSkillSource):
        where = f"{entry.repo}:{entry.path}"
        return f"{where}@{entry.ref}" if entry.ref else where
    return entry


def _probe(
    skill: SkillRef,
    entry: str | GitSkillSource,
    *,
    container: str,
    model: str | None,
    k: int,
    timeout: int,
    workers: int,
    allow_hosts: list[str],
) -> tuple[RunResults, Path]:
    """Run the probes contained, save the run, and return it with its path."""
    backend, skill_model = DEFAULT_BACKEND, None
    if model:
        b, m = parse_target(model)
        backend, skill_model = b or backend, m
    if backend not in VALID_BACKENDS:
        fail(
            CannotRun(
                f"Unknown backend {backend!r} in --model. Known backends: "
                f"{', '.join(sorted(VALID_BACKENDS))}.",
                title="Unknown backend",
            )
        )
    harness = get_harness(backend, skill_model)
    # Never called: every probe is activates-only, so no transcript of an
    # untrusted skill reaches a tool-enabled judge on the host.
    judge = EvalJudge(backend, None, harness=harness)

    spec = probe_spec(entry, skill.name, skill_description(skill.path))
    workdir = Path(tempfile.mkdtemp(prefix="caliper-vet-"))
    spec_path = workdir / f"vet-{skill.name}.eval.yaml"
    spec_path.write_text(spec_yaml(spec))

    console.print(
        f"[cyan]Probing[/cyan] {escape(skill.name)} in {escape(container)} on "
        f"{backend}: {len(spec.tasks)} probes × {k}, canaries planted, egress "
        "limited to the backend's own hosts."
    )
    progress, task_ids = make_progress([t.name for t in spec.tasks], k)
    names = {t.id: t.name for t in spec.tasks}
    seen: dict[str, dict[int, Outcome]] = {t.id: {} for t in spec.tasks}

    def on_attempt_done(event: AttemptEvent) -> None:
        seen[event.task_id][event.attempt] = event.outcome
        update_progress(
            progress, task_ids, names[event.task_id], k, by_attempt=seen[event.task_id]
        )

    aborted: RunAborted | None = None
    try:
        with contain(container, cli=harness.contained_cli()) as contained, progress:
            try:
                results = run(
                    spec=spec,
                    spec_path=spec_path,
                    harness=harness,
                    judge=judge,
                    k=k,
                    workers=workers,
                    timeout=timeout,
                    on_attempt_done=on_attempt_done,
                    container=contained,
                    allow_hosts=allow_hosts,
                )
            except RunAborted as exc:
                aborted, results = exc, exc.results
    except (SkillResolutionError, HarnessConfigurationError) as exc:
        fail(exc)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    run_path = RunStore.discover().save(results)
    if aborted is not None:
        fail(aborted)
    if results.run.interrupted or cancel.requested():
        raise typer.Exit(ExitCode.INTERRUPTED)
    return results, run_path


def _save(report: TrustReport) -> Path:
    store = RunStore.discover()
    directory = store.root / CALIPER_DIR / TRUST_DIR / report.skill
    directory.mkdir(parents=True, exist_ok=True)
    stamp = report.created.strftime("%Y-%m-%dT%H-%M-%SZ")
    path = directory / f"{stamp}.json"
    n = 1
    while path.exists():
        n += 1
        path = directory / f"{stamp}-{n}.json"
    path.write_text(report.model_dump_json(indent=2))
    return path

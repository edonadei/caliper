from pathlib import Path

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.panel import Panel

from caliper.schema.spec import load_spec, spec_name
from caliper.skillfetch import SkillFetcher
from caliper.skills import (
    SkillResolutionError,
    resolve_skills,
    validate_activates,
)

console = Console()


def _supports_unicode() -> bool:
    encoding = getattr(console.file, "encoding", None) or ""
    return "utf" in encoding.lower()


CHECK = "✓" if _supports_unicode() else "OK"
ARROW = "→" if _supports_unicode() else "->"


def validate_cmd(
    spec_file: Path = typer.Argument(..., help="Path to .eval.yaml spec file"),
) -> None:
    if not spec_file.exists():
        console.print(f"[bold red]Error:[/bold red] File not found: {spec_file}")
        raise typer.Exit(1)

    try:
        spec = load_spec(spec_file)
    except ValidationError as exc:
        console.print(
            Panel(
                _format_validation_errors(exc),
                title="[bold red]Validation failed[/bold red]",
                border_style="red",
            )
        )
        raise typer.Exit(1)
    except Exception as exc:
        console.print(f"[bold red]Error parsing YAML:[/bold red] {exc}")
        raise typer.Exit(1)

    # Resolve the neighbourhood here too: a lone slash-command .md, a missing
    # frontmatter name:, or two skills claiming one name are all things
    # `validate` should catch rather than leaving for a paid run to discover.
    # Offline by design: `validate` answers "is this spec well-formed", and a
    # schema check that needs the network is a check you cannot run on a plane.
    # A git source resolves from a warm cache when there is one and is skipped
    # otherwise — never fetched. See docs/adr/0016.
    fetcher = SkillFetcher(offline=True)
    try:
        refs = resolve_skills(list(spec.skills), spec_file.parent, fetcher=fetcher)
        # An uncached git source leaves the neighbourhood *unknown*, not empty,
        # so the closed-set check has to stand down: refusing an `activates:`
        # naming a skill we simply could not see would fail a correct spec for a
        # connectivity reason.
        validate_activates(spec.tasks, refs, closed=not fetcher.unresolved)
    except SkillResolutionError as exc:
        console.print(
            Panel(
                str(exc),
                title="[bold red]Validation failed[/bold red]",
                border_style="red",
            )
        )
        raise typer.Exit(1)

    name = spec_name(spec_file)
    n_tasks = len(spec.tasks)
    asserting = any(t.activates for t in spec.tasks)
    named = ", ".join(ref.name for ref in refs)
    if fetcher.unresolved:
        # Never "bare agent" here: the neighbourhood is *unknown*, not empty,
        # and saying otherwise would describe an offline validate as a spec
        # with no skills in it.
        n = len(fetcher.unresolved)
        uncached = f"[dim]{n} git source{'' if n == 1 else 's'} not cached[/dim]"
        skills = f"{named}, {uncached}" if named else uncached
    else:
        skills = named or "(bare agent — no skills)"
    asserted = sum(1 for t in spec.tasks if t.activates is not None)
    # Standing the closure check down is invisible otherwise, and an author
    # would read a green panel as "the names in activates: are known good".
    # Say so instead: the check is deferred to the run, which has fetched.
    caveat = (
        "\n  [yellow]note[/yellow]     [dim]activates: names not checked — a "
        "git source is uncached, so the\n           neighbourhood is not fully "
        "known here. `caliper run` checks them.[/dim]"
        if fetcher.unresolved and asserting
        else ""
    )

    console.print(
        Panel(
            f"[bold]{name}[/bold]\n"
            f"  skills   [cyan]{skills}[/cyan]\n"
            f"  tasks    [cyan]{n_tasks}[/cyan] "
            f"[dim]({asserted} asserting activates:)[/dim]\n"
            "  engine   [dim]chosen at run time (--model / --judge-model)[/dim]"
            f"{caveat}",
            title=f"[bold green]{CHECK} Spec is valid[/bold green]",
            border_style="green",
        )
    )


def _format_validation_errors(exc: ValidationError) -> str:
    lines = []
    for err in exc.errors():
        loc = f" {ARROW} ".join(str(p) for p in err["loc"])
        lines.append(f"  [dim]{loc}[/dim]  {err['msg']}")
    return "\n".join(lines)

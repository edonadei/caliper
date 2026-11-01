"""Opt-out controls. These commands never emit telemetry themselves."""

import typer

from caliper import telemetry
from caliper.commands.diagnosis import CannotRun, fail

app = typer.Typer(
    help="Control anonymous CLI usage metrics (on by default; opt out here).",
    no_args_is_help=True,
)


@app.command()
def status() -> None:
    """Show the saved preference, environment overrides, and delivery status."""
    preference = telemetry.preference()
    target = telemetry.destination()
    saved = {None: "none yet", True: "enabled", False: "disabled"}[preference]
    typer.echo(f"Saved preference: {saved}")
    if telemetry.forced_off():
        typer.echo("Collection disabled by CALIPER_TELEMETRY_DISABLED or DO_NOT_TRACK.")
    elif preference is False:
        typer.echo("Collection disabled. Re-enable with: caliper telemetry enable")
    elif preference is None and telemetry.in_ci():
        typer.echo("Collection disabled in CI unless enabled explicitly.")
    elif preference is None:
        typer.echo(
            "Collection starts after the next command, which shows a one-time "
            "notice. Opt out with: caliper telemetry disable"
        )
    elif target is None:
        typer.echo("Collection inactive: no valid PostHog project is configured.")
    else:
        host, _ = target
        typer.echo(f"Collection enabled. Destination: https://{host}")


def _save(enabled: bool) -> None:
    try:
        telemetry.set_enabled(enabled)
    except (OSError, RuntimeError):
        fail(
            CannotRun(
                "Cannot save telemetry preference in Caliper's config directory. "
                "Set CALIPER_TELEMETRY_DISABLED=1 or DO_NOT_TRACK=1 to disable "
                "collection."
            )
        )


@app.command()
def enable() -> None:
    """Share command, version, OS, backend, outcome and duration buckets."""
    _save(True)
    typer.echo("Enabled anonymous CLI usage metrics sent to PostHog.")
    typer.echo(
        "Only command, Caliper version, OS family, backend, outcome and duration bucket are sent."
    )
    typer.echo(
        "No prompts, outputs, paths, arguments, error text or installation ID are sent."
    )
    status()


@app.command()
def disable() -> None:
    """Opt out. No telemetry requests are made while disabled."""
    _save(False)
    typer.echo("Telemetry disabled.")

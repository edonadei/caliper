"""Explicit consent controls. These commands never emit telemetry themselves."""

import typer

from caliper import telemetry
from caliper.commands.diagnosis import CannotRun, fail

app = typer.Typer(
    help="Control optional, anonymous CLI usage metrics.", no_args_is_help=True
)


@app.command()
def status() -> None:
    """Show saved consent, the environment override, and delivery status."""
    preference = telemetry.enabled()
    target = telemetry.destination()
    typer.echo(f"Saved preference: {'enabled' if preference else 'disabled'}")
    if telemetry.forced_off():
        typer.echo("Collection disabled by CALIPER_TELEMETRY_DISABLED.")
    elif not preference:
        typer.echo("Collection disabled. Opt in with: caliper telemetry enable")
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
                "Set CALIPER_TELEMETRY_DISABLED=1 to disable collection."
            )
        )


@app.command()
def enable() -> None:
    """Opt in to command, version, OS, backend, outcome and duration buckets."""
    _save(True)
    typer.echo("Saved opt-in to anonymous CLI usage metrics sent to PostHog.")
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

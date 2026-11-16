import contextlib
from collections.abc import Generator
from typing import Any

import typer
from typer.core import TyperGroup

from caliper.commands.compare import compare_cmd
from caliper.commands.diagnosis import ExitCode
from caliper.commands.list_cmd import list_cmd_fn
from caliper.commands.login import login_cmd, logout_cmd
from caliper.commands.report import report_cmd
from caliper.commands.run import run_cmd
from caliper.commands.telemetry import app as telemetry_app
from caliper.commands.validate import validate_cmd
from caliper.telemetry import TelemetryCommand

# Whichever click typer raises from: its own vendored copy from 0.26, else click.
_USAGE_ERROR = next(
    c
    for c in typer.BadParameter.__mro__
    if issubclass(c, Exception) and c.__name__ == "UsageError"
)


class _CaliperGroup(TyperGroup):
    """Exits a usage error, such as an unknown flag or value, as bad input.

    Click exits it 2, which the README's exit codes reserve for "could not run".
    """

    def parse_args(self, ctx, args):
        with _usage_errors_are_bad_input():
            return super().parse_args(ctx, args)

    def invoke(self, ctx):
        with _usage_errors_are_bad_input():
            return super().invoke(ctx)


@contextlib.contextmanager
def _usage_errors_are_bad_input() -> Generator[None, None, None]:
    try:
        yield
    except _USAGE_ERROR as error:
        usage: Any = error  # click types exit_code as a class constant
        usage.exit_code = ExitCode.BAD_INPUT
        raise


app = typer.Typer(
    name="caliper",
    cls=_CaliperGroup,
    help="[bold cyan]caliper[/bold cyan] — evaluate AI skills with confidence",
    rich_markup_mode="rich",
    no_args_is_help=True,
)

app.command("run", cls=TelemetryCommand, help="Run an evaluation spec")(run_cmd)
app.command("report", cls=TelemetryCommand, help="Render saved evaluation results")(
    report_cmd
)
app.command(
    "compare",
    cls=TelemetryCommand,
    help="Diff two saved runs of the same eval (A vs B)",
)(compare_cmd)
app.command("list", cls=TelemetryCommand, help="List evaluation specs and past runs")(
    list_cmd_fn
)
app.command("validate", cls=TelemetryCommand, help="Validate an evaluation spec file")(
    validate_cmd
)
app.command(
    "login", cls=TelemetryCommand, help="Sign in to ChatGPT so codex runs use your plan"
)(login_cmd)
app.command("logout", cls=TelemetryCommand, help="Sign out of ChatGPT")(logout_cmd)
app.add_typer(telemetry_app, name="telemetry")


def main() -> None:
    app()


if __name__ == "__main__":
    main()

import typer

from caliper.commands.compare import compare_cmd
from caliper.commands.list_cmd import list_cmd_fn
from caliper.commands.login import login_cmd, logout_cmd
from caliper.commands.report import report_cmd
from caliper.commands.run import run_cmd
from caliper.commands.telemetry import app as telemetry_app
from caliper.commands.validate import validate_cmd
from caliper.telemetry import TelemetryCommand

app = typer.Typer(
    name="caliper",
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

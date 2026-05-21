import typer

from verdict.commands.run import run_cmd
from verdict.commands.new import new_cmd
from verdict.commands.report import report_cmd
from verdict.commands.list_cmd import list_cmd_fn
from verdict.commands.validate import validate_cmd

app = typer.Typer(
    name="verdict",
    help="[bold cyan]verdict[/bold cyan] — evaluate AI skills with confidence",
    rich_markup_mode="rich",
    no_args_is_help=True,
)

app.command("run", help="Run an evaluation spec")(run_cmd)
app.command("new", help="Create a new evaluation spec (interactive wizard)")(new_cmd)
app.command("report", help="Render saved evaluation results")(report_cmd)
app.command("list", help="List evaluation specs and past runs")(list_cmd_fn)
app.command("validate", help="Validate an evaluation spec file")(validate_cmd)


def main() -> None:
    app()

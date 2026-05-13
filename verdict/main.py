import typer
from rich.console import Console
from rich import print as rprint

from verdict.commands import run, new, report, list_cmd, validate

app = typer.Typer(
    name="verdict",
    help="[bold cyan]verdict[/bold cyan] — evaluate AI skills with confidence",
    rich_markup_mode="rich",
    no_args_is_help=True,
)

app.add_typer(run.app, name="run")
app.add_typer(new.app, name="new")
app.add_typer(report.app, name="report")
app.add_typer(list_cmd.app, name="list")
app.add_typer(validate.app, name="validate")


def main() -> None:
    app()

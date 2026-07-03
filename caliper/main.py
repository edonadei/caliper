import typer

from caliper.commands.run import run_cmd
from caliper.commands.new import new_cmd
from caliper.commands.report import report_cmd
from caliper.commands.compare import compare_cmd
from caliper.commands.list_cmd import list_cmd_fn
from caliper.commands.validate import validate_cmd
from caliper.commands.update_cli import update_cli_cmd

app = typer.Typer(
    name="caliper",
    help="[bold cyan]caliper[/bold cyan] — evaluate AI skills with confidence",
    rich_markup_mode="rich",
    no_args_is_help=True,
)

app.command("run", help="Run an evaluation spec")(run_cmd)
app.command("new", help="Create a new evaluation spec (interactive wizard)")(new_cmd)
app.command("report", help="Render saved evaluation results")(report_cmd)
app.command("compare", help="Diff two saved runs of the same eval (A vs B)")(
    compare_cmd
)
app.command("list", help="List evaluation specs and past runs")(list_cmd_fn)
app.command("validate", help="Validate an evaluation spec file")(validate_cmd)
app.command("update-cli", help="Check or update Codex and Claude Code CLIs")(
    update_cli_cmd
)


def main() -> None:
    app()


if __name__ == "__main__":
    main()

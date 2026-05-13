import typer

app = typer.Typer(help="Run an evaluation spec")


@app.callback(invoke_without_command=True)
def run_cmd() -> None:
    pass

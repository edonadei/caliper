import typer

app = typer.Typer(help="Validate an evaluation spec file")


@app.callback(invoke_without_command=True)
def validate_cmd() -> None:
    pass

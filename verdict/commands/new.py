import typer

app = typer.Typer(help="Create a new evaluation spec (interactive wizard)")


@app.callback(invoke_without_command=True)
def new_cmd() -> None:
    pass

import typer

app = typer.Typer(help="List evaluation specs and past runs")


@app.callback(invoke_without_command=True)
def list_cmd_fn() -> None:
    pass

import typer

app = typer.Typer(help="Render saved evaluation results")


@app.callback(invoke_without_command=True)
def report_cmd() -> None:
    pass

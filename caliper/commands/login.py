from __future__ import annotations

import webbrowser
from enum import Enum
from typing import Annotated

import typer
from rich.console import Console

from caliper.chatgpt import ChatGPTLoginError, chatgpt_account
from caliper.commands.diagnosis import CannotRun, fail

console = Console()


class Provider(str, Enum):
    """Who caliper signs in to. A typer choice, so another name is refused."""

    CHATGPT = "chatgpt"


ProviderArgument = Annotated[Provider, typer.Argument(help="What to sign in to")]


def login_cmd(provider: ProviderArgument) -> None:
    """Sign caliper in to ChatGPT, so codex runs use your plan (docs/backends.md)."""

    def open_browser(url: str) -> None:
        console.print("Opening your browser to sign in with ChatGPT.")
        console.print("If it doesn't open, visit:")
        console.print(f"  {url}", soft_wrap=True, markup=False, highlight=False)
        webbrowser.open(url)

    try:
        email, error = chatgpt_account().sign_in(open_browser), None
    except ChatGPTLoginError as exc:
        email, error = None, exc
    if error is not None:
        fail(CannotRun(str(error)))
    console.print(f"Signed in as {email}. Codex runs now use your ChatGPT plan.")


def logout_cmd(provider: ProviderArgument) -> None:
    """Revoke caliper's ChatGPT login and forget it."""
    account = chatgpt_account()
    if not account.signed_in:
        console.print("Not signed in to ChatGPT.")
        return
    if account.sign_out():
        console.print(
            "Signed out of ChatGPT. Codex runs use your Codex CLI login again."
        )
        return
    console.print(
        "Signed out here, but OpenAI did not confirm it revoked the session. "
        "Disconnect Caliper in your ChatGPT settings to be sure. Codex runs use "
        "your Codex CLI login again.",
        soft_wrap=True,
    )

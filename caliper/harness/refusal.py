"""CLI refusals: the agent's CLI declining to run an attempt.

A refusal is something the CLI said, not something the agent wrote: the
provider is busy, the account is out of budget, or the CLI is misconfigured.
See docs/CONTEXT.md → CLI refusal.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum

# Provider signals that mean the skill was never fairly run. Matched even on a
# zero exit, because these typically let the CLI exit 0 with the message as its
# only "output" — but only over what the CLI wrote (see ``classify``).
#
# Split in two because the right response differs, not because the labels do —
# both still earn INFRA_ERROR when they reach the end of the line. The question
# each half answers is "would another invocation behave differently?"

# Yes: the provider is busy or we are going too fast. Worth retrying in seconds
# (docs/adr/0019-an-attempt-may-be-invoked-more-than-once.md).
_THROTTLE_SIGNALS = re.compile(
    r"rate.?limit|\b429\b|overloaded|too many requests|service unavailable"
    r"|\b503\b|\b529\b",
    re.IGNORECASE,
)

# No: the account is out of budget, and every remaining attempt of the run would
# meet the same wall. Aborts the run rather than being retried or repeated.
_CAP_SIGNALS = re.compile(
    r"spending cap|quota (?:exceeded|reached)|usage limit",
    re.IGNORECASE,
)


def looks_like_throttle(text: str) -> bool:
    """True when free text says the provider is busy — a retryable signal."""
    return bool(text) and bool(_THROTTLE_SIGNALS.search(text))


def looks_like_spending_cap(text: str) -> bool:
    """True when free text says the account is out of budget.

    Not retryable and not survivable: the cap does not clear inside a run, so
    the runner aborts rather than spending every remaining attempt discovering
    the same wall. See docs/adr/0019.
    """
    return bool(text) and bool(_CAP_SIGNALS.search(text))


def spending_cap_line(text: str) -> str:
    """The line of ``text`` that says the account is out of budget.

    What the abort quotes. A CLI's first line is usually its own chatter (codex
    opens with ``thread.started``), while the line that matched is the one that
    says when the limit resets.
    """
    lines = text.strip().splitlines()
    line = next((line for line in lines if _CAP_SIGNALS.search(line)), lines[0])
    # A JSON event line is quoted by the string inside it that says so — codex's
    # top-level `message`, pi's `message.errorMessage` — so the reset time
    # survives the length cap instead of a dump of the event.
    try:
        event = json.loads(line)
    except ValueError:
        return line
    return next((text for text in _strings(event) if _CAP_SIGNALS.search(text)), line)


def _strings(value: object) -> list[str]:
    """Every string in a parsed JSON value, depth first."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, list):
        return [text for item in value for text in _strings(item)]
    return []


class RefusalKind(Enum):
    """Why the CLI refused, which decides what the run does about it.

    ``SPENDING_CAP`` stops the run: no attempt left would behave differently.
    ``THROTTLE`` is retried: the next invocation might (docs/adr/0019).
    ``CONFIG`` stops the run before another attempt is spent on it.
    """

    SPENDING_CAP = "spending_cap"
    THROTTLE = "throttle"
    CONFIG = "config"


@dataclass(frozen=True)
class CliRefusal:
    kind: RefusalKind
    # What to tell the user: the line that names a cap, the throttle text, or
    # the full diagnosis of a misconfiguration.
    message: str


@dataclass(frozen=True)
class ConfigSignal:
    """A misconfiguration a backend's CLI reports, and what to do about it.

    ``diagnosis`` is shown when any marker appears in what the CLI wrote;
    ``{text}`` in it is replaced by that text.
    """

    markers: tuple[str, ...]
    diagnosis: str


# The words every CLI uses for a login that is missing or has lapsed.
AUTH_MARKERS = (
    "401",
    "unauthorized",
    "not logged in",
    "please login",
    "please run /login",
    "authentication",
    "invalid api key",
    "subscription",
)

# Enough of what the CLI wrote to act on, without dumping a whole stream.
_QUOTED = 1000


def classify(
    cli_text: str,
    config_signals: Sequence[ConfigSignal],
    diagnose: Callable[[str], str | None] | None = None,
) -> CliRefusal | None:
    """The refusal in what the CLI wrote, or ``None`` if it wrote none.

    ``cli_text`` must be only what the CLI itself said — its stderr, its own
    error events, output nothing parsed as the agent's — never the agent's
    answer: an agent can write "not logged in" or "rate limit" about the task it
    was given (docs/adr/0030). Checked in one order — cap, throttle, the
    backend's own ``diagnose`` of the text's structure, then its
    ``config_signals`` — so a cap is never diagnosed as a bad login because it
    also mentions the subscription.
    """
    text = cli_text.strip()
    if not text:
        return None
    if looks_like_spending_cap(text):
        return CliRefusal(RefusalKind.SPENDING_CAP, spending_cap_line(text))
    if looks_like_throttle(text):
        return CliRefusal(RefusalKind.THROTTLE, text[:_QUOTED])
    diagnosis = diagnose(text) if diagnose is not None else None
    if diagnosis:
        return CliRefusal(RefusalKind.CONFIG, diagnosis)
    lowered = text.lower()
    for signal in config_signals:
        if any(marker in lowered for marker in signal.markers):
            diagnosis = signal.diagnosis.replace("{text}", text[:_QUOTED])
            return CliRefusal(RefusalKind.CONFIG, diagnosis)
    return None

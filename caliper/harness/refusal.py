"""CLI refusals: the agent's CLI declining to run an attempt.

A refusal is something the CLI said, not something the agent wrote: the
provider is busy, the account is out of budget, or the CLI is misconfigured.
See docs/CONTEXT.md → CLI refusal.
"""

from __future__ import annotations

import json
import re

# Mid-run harness signals that mean the skill was never fairly run. Matched over
# the attempt's output + error even on a zero exit, because these typically let
# the CLI exit 0 with the message as its only "output". Startup auth / login
# misconfiguration is deliberately NOT here: that raises
# HarnessConfigurationError and aborts the whole run instead.
#
# Split in two because the right response differs, not because the labels do —
# both still earn INFRA_ERROR when they reach the end of the line. The question
# each half answers is "would another invocation behave differently?"

# Yes: the provider is busy or we are going too fast. Worth retrying in seconds
# (docs/adr/0019-an-attempt-may-be-invoked-more-than-once.md).
_THROTTLE_SIGNALS = re.compile(
    r"rate.?limit|\b429\b|overloaded|too many requests|service unavailable|\b503\b",
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


def looks_like_infra_failure(text: str) -> bool:
    """True when free text carries a transient throttle/overload signal.

    The single source of truth for infra detection, used by ``classify_pre_judge``
    to label the attempt. The union of both halves above: how an attempt is
    *labelled* is unchanged by the split, which only decides what the retry seam
    does before the label is reached.
    """
    return looks_like_throttle(text) or looks_like_spending_cap(text)

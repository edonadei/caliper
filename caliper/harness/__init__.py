import importlib

from caliper.backends import BACKENDS, normalize_backend
from caliper.harness.base import (
    AttemptResult,
    CliHarness,
    ConversationTurn,
    HarnessBackend,
)


def get_harness(
    backend: str, model: str | None = None, effort: str | None = None
) -> HarnessBackend:
    entry = BACKENDS.get(normalize_backend(backend))
    if entry is None:
        raise ValueError(f"Unknown backend: {backend!r}")
    module, _, cls = entry.harness.partition(":")
    return getattr(importlib.import_module(module), cls)(model=model, effort=effort)


__all__ = [
    "AttemptResult",
    "CliHarness",
    "ConversationTurn",
    "HarnessBackend",
    "get_harness",
]

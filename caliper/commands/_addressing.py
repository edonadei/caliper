"""Shared run addressing for commands that load a saved run (``report``, ``compare``).

A run reference is either a direct path to a results JSON, or a spec name that
resolves to ``.caliper/results/<name>/`` — the latest run, or a specific one via
``--run <timestamp>``. Both commands resolve their positional arguments the same
way through :func:`resolve_run_path`.
"""

from __future__ import annotations

from pathlib import Path


def resolve_run_path(spec_or_file: str, run: str | None = None) -> Path | None:
    """Resolve a run reference to a results-JSON path, or ``None`` if not found."""
    p = Path(spec_or_file)

    # Direct JSON path.
    if p.suffix == ".json" and p.exists():
        return p

    # Spec name -> look in .caliper/results/<name>/
    results_dir = Path(".caliper") / "results" / spec_or_file
    if not results_dir.exists():
        return None

    if run:
        candidate = results_dir / f"{run}.json"
        return candidate if candidate.exists() else None

    # Latest = lexicographically last (ISO timestamps sort correctly).
    files = sorted(results_dir.glob("*.json"))
    return files[-1] if files else None

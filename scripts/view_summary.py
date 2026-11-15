"""Print a PR's terminal views as Markdown for the job summary, before and after.

Images load from the two commits' raw files, which the README freshness test
keeps equal to what the reporter renders. Usage:
``python scripts/view_summary.py <base-repo> <base-sha> <head-repo> <head-sha>``.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = "docs/assets"
RAW = "https://raw.githubusercontent.com"


def views() -> list[str]:
    """The SVGs `docs/render_readme_samples.py` renders from the reporter."""
    path = ROOT / "docs/render_readme_samples.py"
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return sorted(module.render_all())


def summary(names: list[str], changed: set[str], before: str, after: str) -> str:
    """Changed views as before/after images; the rest folded, as they are now."""
    out = ["## Terminal views", ""]
    shown = [name for name in names if name in changed]
    if not shown:
        out += ["This PR changes no terminal view.", ""]
    for name in shown:
        out += [
            f"### `{name}`",
            "",
            "**Before**",
            "",
            f"![before]({before}/{ASSETS}/{name})",
            "",
            "**After**",
            "",
            f"![after]({after}/{ASSETS}/{name})",
            "",
        ]
    folded = [name for name in names if name not in changed]
    if folded:
        count = f"{len(folded)} view" + ("s" if len(folded) > 1 else "")
        out += ["<details>", f"<summary>Unchanged: {count}</summary>", ""]
        out += [f"![{name}]({after}/{ASSETS}/{name})\n" for name in folded]
        out += ["</details>", ""]
    return "\n".join(out)


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def main(argv: list[str]) -> int:
    base_repo, base_sha, head_repo, head_sha = argv
    # "Before" is where the PR branched off, as the PR's own diff shows it.
    fork_point = _git("merge-base", base_sha, head_sha)
    changed = {
        Path(path).name
        for path in _git(
            "diff", "--name-only", fork_point, head_sha, "--", ASSETS
        ).splitlines()
    }
    print(
        summary(
            views(),
            changed,
            f"{RAW}/{base_repo}/{fork_point}",
            f"{RAW}/{head_repo}/{head_sha}",
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

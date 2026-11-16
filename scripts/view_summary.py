"""Print a PR's terminal views as Markdown for the job summary, before and after.

Images load from the two commits' raw files, which the README freshness test
keeps equal to what the scripts render. Usage:
``python scripts/view_summary.py <base-repo> <base-sha> <head-repo> <head-sha>``.
It reruns itself as ``view_summary.py --views <tree>`` to list a commit's views.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = "docs/assets"
RAW = "https://raw.githubusercontent.com"
RENDERERS = ("docs/render_readme_samples.py", "docs/render_readme_hero.py")
# Rich's class prefix hashes its own version; tests/test_readme_renders.py
# ignores it the same way.
_RICH_HASH_RE = re.compile(r"\bterminal-\d+-")


def views(root: Path = ROOT) -> list[str]:
    """The SVGs the README scripts under ``root`` render into ``docs/assets``."""
    names: set[str] = set()
    for rel_path in RENDERERS:
        path = root / rel_path
        if not path.exists():
            continue
        spec = importlib.util.spec_from_file_location(path.stem, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        # Scripts older than #352 write their files rather than return them.
        if hasattr(module, "render_all"):
            names |= set(module.render_all())
    return sorted(names)


def state(before: str | None, after: str | None) -> str:
    """``new``, ``removed``, ``changed`` or ``unchanged``, ignoring Rich's hash."""
    if before is None:
        return "new"
    if after is None:
        return "removed"
    same = _RICH_HASH_RE.sub("", before) == _RICH_HASH_RE.sub("", after)
    return "unchanged" if same else "changed"


def summary(states: dict[str, str], before_url: str, after_url: str) -> str:
    """Views that moved as images; the unchanged ones folded, as they are now."""
    out = ["## Terminal views", ""]
    moved = [name for name, s in sorted(states.items()) if s != "unchanged"]
    if not moved:
        out += ["This PR changes none of the sampled views in the README.", ""]
    for name in moved:
        images = {
            "changed": [("before", before_url), ("after", after_url)],
            "new": [("new", after_url)],
            "removed": [("removed", before_url)],
        }[states[name]]
        out += [f"### `{name}`", ""]
        for label, url in images:
            out += [
                f"**{label.capitalize()}**",
                "",
                f"![{label}]({url}/{ASSETS}/{name})",
                "",
            ]
    folded = [name for name, s in sorted(states.items()) if s == "unchanged"]
    if folded:
        count = f"{len(folded)} view" + ("s" if len(folded) > 1 else "")
        out += ["<details>", f"<summary>Unchanged: {count}</summary>", ""]
        out += [f"![{name}]({after_url}/{ASSETS}/{name})\n" for name in folded]
        out += ["</details>", ""]
    return "\n".join(out)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def _show(root: Path, commit: str, name: str) -> str | None:
    proc = subprocess.run(
        ["git", "show", f"{commit}:{ASSETS}/{name}"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    return proc.stdout if proc.returncode == 0 else None


def _views_at(root: Path, commit: str) -> set[str]:
    """The views ``commit``'s own scripts rendered, run against its own caliper."""
    with tempfile.TemporaryDirectory() as tree:
        _git(root, "worktree", "add", "-q", "--detach", tree, commit)
        try:
            env = {**os.environ, "PYTHONPATH": tree}
            out = subprocess.run(
                [sys.executable, __file__, "--views", tree],
                env=env,
                check=True,
                stdout=subprocess.PIPE,
                text=True,
            ).stdout
        finally:
            _git(root, "worktree", "remove", "--force", tree)
    return set(json.loads(out))


def main(argv: list[str], root: Path = ROOT) -> int:
    if argv[0] == "--views":
        print(json.dumps(views(Path(argv[1]))))
        return 0
    base_repo, base_sha, head_repo, head_sha = argv
    # "Before" is where the PR branched off, as the PR's own diff shows it.
    fork_point = _git(root, "merge-base", base_sha, head_sha)
    # Each side's own scripts: CI checks out the merge commit, not the head.
    names = _views_at(root, fork_point) | _views_at(root, head_sha)
    states = {
        name: state(_show(root, fork_point, name), _show(root, head_sha, name))
        for name in names
    }
    print(
        summary(
            states, f"{RAW}/{base_repo}/{fork_point}", f"{RAW}/{head_repo}/{head_sha}"
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

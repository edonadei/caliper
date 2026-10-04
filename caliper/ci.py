"""The GitHub Action's driver: pick specs, cap the spend, run, compare, comment.

``action.yml`` at the repo root installs caliper and the agent CLIs, then hands
over to ``python -m caliper.ci``. Everything with a decision in it lives here,
where it can be tested, rather than in workflow shell.

The run's own verdict is still ``caliper run``'s exit code (README → Exit codes):
this module only aggregates it across specs. ``3`` comes from a spec's
pre-registered ``bar:`` (docs/adr/0035), never from the base comparison — a
regression flag fires on any drop, which at CI sample sizes is noise as often as
signal (docs/CONTEXT.md → Regression).

Inputs arrive as environment variables, the way a composite action passes them:
``CALIPER_SPECS``, ``CALIPER_K``, … (see ``Config.from_env``).
"""

from __future__ import annotations

import glob
import json
import os
import shlex
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from caliper.schema.spec import (
    DEFAULT_BACKEND,
    EvalSpec,
    assert_script_path,
    load_spec,
    parse_target,
    spec_name,
)

# Hidden in the comment body, so a re-run edits its own comment instead of
# stacking a new one per push.
COMMENT_MARKER = "<!-- caliper-action -->"

# One file per spec: this job's runs (head), and the default branch's latest
# runs a pull request is compared against (base, kept in the Actions cache).
# Under .caliper/results/ so the sandbox's saved-results rule keeps every
# attempt off them — a later spec's agent must not read an earlier spec's
# answers (caliper/sandbox.py). One level down, so `caliper list` never shows
# them as a spec.
CI_DIR = Path(".caliper/results/.ci")
BASE_DIR = CI_DIR / "base"
HEAD_DIR = CI_DIR / "head"


def _say(message: str) -> None:
    # stdout: the runner reads workflow commands (::group::, ::warning::) there.
    sys.stdout.write(message + "\n")
    sys.stdout.flush()


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


@dataclass
class Config:
    specs: list[str]
    k: int
    max_attempts: int
    changed_only: bool
    model: str = ""
    judge_model: str = ""
    extra_args: list[str] = field(default_factory=list)
    base_sha: str = ""
    is_pull_request: bool = False
    is_default_branch_push: bool = False
    comment: bool = True

    @classmethod
    def from_env(cls) -> Config:
        event = os.environ.get("GITHUB_EVENT_NAME", "")
        ref = os.environ.get("GITHUB_REF", "")
        default_branch = os.environ.get("CALIPER_DEFAULT_BRANCH", "")
        return cls(
            specs=os.environ.get("CALIPER_SPECS", "**/*.eval.yaml").split(),
            k=int(os.environ.get("CALIPER_K") or 3),
            max_attempts=int(os.environ.get("CALIPER_MAX_ATTEMPTS") or 0),
            changed_only=_flag("CALIPER_CHANGED_ONLY", True),
            model=os.environ.get("CALIPER_MODEL", ""),
            judge_model=os.environ.get("CALIPER_JUDGE_MODEL", ""),
            extra_args=shlex.split(os.environ.get("CALIPER_EXTRA_ARGS", "")),
            base_sha=os.environ.get("CALIPER_BASE_SHA", ""),
            is_pull_request=event in ("pull_request", "pull_request_target"),
            is_default_branch_push=event == "push"
            and bool(default_branch)
            and ref == f"refs/heads/{default_branch}",
            comment=_flag("CALIPER_COMMENT", True),
        )


# ── which specs ─────────────────────────────────────────────────────────────

_SKIP_DIRS = {".git", "node_modules", ".venv", ".caliper"}


def find_specs(patterns: list[str], root: Path) -> list[Path]:
    found: set[Path] = set()
    for pattern in patterns:
        for match in glob.glob(pattern, root_dir=root, recursive=True):
            path = Path(match)
            if _SKIP_DIRS & set(path.parts):
                continue
            if path.name.endswith((".eval.yaml", ".eval.yml")):
                found.add(path)
    return sorted(found)


def spec_inputs(spec_path: Path, spec: EvalSpec) -> list[PurePosixPath]:
    """The repo paths whose change can move this spec's score.

    The spec's own directory (prompts, fixtures, assert scripts beside it), each
    path-sourced skill's directory (its SKILL.md and the files it discloses),
    and any assert script that lives elsewhere. A git source is not a repo path:
    its drift is reported by ``compare`` rather than detected here
    (docs/adr/0017).
    """
    spec_dir = spec_path.parent
    dirs = {spec_dir}
    for source in spec.skills:
        if isinstance(source, str):
            dirs.add((spec_dir / source).parent)
    for task in spec.tasks:
        if task.assert_script:
            script = assert_script_path(task.assert_script, spec_dir)
            if script is not None:
                dirs.add(script)
    return sorted(
        {PurePosixPath(os.path.normpath(d).replace(os.sep, "/")) for d in dirs}
    )


def touches(changed: list[str], inputs: list[PurePosixPath]) -> bool:
    for name in changed:
        path = PurePosixPath(name)
        for inp in inputs:
            if str(inp) in ("", ".") or path == inp or inp in path.parents:
                return True
    return False


def changed_files(base_sha: str) -> list[str] | None:
    """Files this pull request changes, or ``None`` when git cannot say.

    Diffs the checked-out commit — GitHub's merge of the PR into its base —
    against the base commit, fetching that commit if the checkout is shallow.
    """
    if not base_sha:
        return None
    subprocess.run(
        ["git", "fetch", "--no-tags", "--depth=1", "origin", base_sha],
        capture_output=True,
        check=False,
    )
    diff = subprocess.run(
        ["git", "diff", "--name-only", base_sha, "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if diff.returncode != 0:
        return None
    return [line for line in diff.stdout.splitlines() if line.strip()]


def select_specs(
    candidates: list[Path], changed: list[str] | None
) -> tuple[list[Path], list[str]]:
    """The specs to run, and a note per spec skipped or refused.

    ``changed`` of ``None`` runs everything: when the diff is unknown, running
    too much costs money, and running too little lets a regression through.
    """
    selected: list[Path] = []
    notes: list[str] = []
    for path in candidates:
        try:
            spec = load_spec(path)
        except Exception as exc:
            # Kept in the run: `caliper run` gives the real diagnosis (exit 1).
            notes.append(f"`{path}` does not load ({exc.__class__.__name__})")
            selected.append(path)
            continue
        if changed is None or touches(changed, spec_inputs(path, spec)):
            selected.append(path)
    return selected, notes


def planned_attempts(specs: list[Path], k: int) -> int:
    total = 0
    for path in specs:
        try:
            total += len(load_spec(path).tasks) * k
        except Exception:
            continue
    return total


# ── the pre-registered bar ──────────────────────────────────────────────────


def bar_change(spec_path: Path, base_sha: str) -> str | None:
    """A note when this PR edits the spec's ``bar:``, else ``None``.

    The bar is pre-registered by being committed before the change it judges
    (docs/adr/0035). A PR that moves the bar *and* the skill grades itself, so
    the comment says so where the reviewer will see it.
    """
    if not base_sha:
        return None
    shown = subprocess.run(
        ["git", "show", f"{base_sha}:{spec_path.as_posix()}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if shown.returncode != 0:
        return None
    import yaml

    try:
        before = (yaml.safe_load(shown.stdout) or {}).get("bar")
        after = (yaml.safe_load(spec_path.read_text()) or {}).get("bar")
    except Exception:
        return None
    if before == after:
        return None
    return (
        f"This PR changes `bar:` in `{spec_path}` ({before!r} → {after!r}). A bar "
        "is pre-registered by landing before the change it judges; consider "
        "moving it to its own PR."
    )


# ── running ─────────────────────────────────────────────────────────────────


@dataclass
class SpecRun:
    path: Path
    code: int
    head: Path | None
    base: Path | None
    bar_note: str | None = None

    @property
    def name(self) -> str:
        return spec_name(self.path)


def run_spec(path: Path, config: Config) -> SpecRun:
    name = spec_name(path)
    head = HEAD_DIR / f"{name}.json"
    head.unlink(missing_ok=True)
    cmd = ["caliper", "run", str(path), "--k", str(config.k), "--output", str(head)]
    if config.model:
        cmd += ["--model", config.model]
    if config.judge_model:
        cmd += ["--judge-model", config.judge_model]
    cmd += config.extra_args
    _say(f"::group::caliper run {path}")
    _say("$ " + shlex.join(cmd))
    code = subprocess.run(cmd, check=False).returncode
    _say("::endgroup::")
    base = BASE_DIR / f"{name}.json"
    return SpecRun(
        path=path,
        code=code,
        head=head if head.is_file() else None,
        base=base if base.is_file() else None,
        bar_note=bar_change(path, config.base_sha) if config.is_pull_request else None,
    )


def _caliper_markdown(*args: str) -> str | None:
    out = subprocess.run(
        ["caliper", *args, "--format", "markdown"],
        capture_output=True,
        text=True,
        check=False,
    )
    return out.stdout if out.returncode == 0 else None


_CODE_MEANING = {
    0: "ran",
    1: "bad input",
    2: "could not run",
    3: "missed the bar",
    130: "interrupted",
}


def render(runs: list[SpecRun], notes: list[str], *, compare: bool) -> str:
    lines = [COMMENT_MARKER, "## Caliper", ""]
    if not runs:
        lines.append("No spec's inputs changed in this PR — nothing ran.")
    for spec_run in runs:
        meaning = _CODE_MEANING.get(spec_run.code, "failed")
        if spec_run.head is None:
            lines += [
                f"### `{spec_run.name}`",
                "",
                f"**exit {spec_run.code}** ({meaning}) — no results were saved; "
                "see the job log.",
                "",
            ]
            continue
        report = _caliper_markdown("report", str(spec_run.head))
        lines.append(report or f"### `{spec_run.name}`\n")
        if spec_run.code not in (0, 3):
            lines.append(f"**exit {spec_run.code}** ({meaning}) — see the job log.")
        if spec_run.bar_note:
            lines += ["", f"> ⚠️ {spec_run.bar_note}"]
        if compare:
            if spec_run.base is None:
                lines += [
                    "",
                    "_No base run from the default branch to compare with yet._",
                ]
            else:
                diff = _caliper_markdown(
                    "compare", str(spec_run.base), str(spec_run.head)
                )
                lines += [
                    "",
                    "<details><summary>Compared with the latest run on the "
                    "default branch</summary>",
                    "",
                    diff or "_The two runs could not be compared; see the job log._",
                    "</details>",
                ]
        lines.append("")
    if notes:
        lines += ["", *(f"- {note}" for note in notes)]
    return "\n".join(lines).rstrip() + "\n"


def overall_code(codes: list[int]) -> int:
    """One exit for the job: 3 if any spec missed its bar, else the worst other.

    A missed bar is reported over a broken sibling because it is the answer the
    workflow exists for; the broken one is still red in the comment and log.
    """
    failing = [c for c in codes if c != 0]
    if not failing:
        return 0
    if 3 in failing:
        return 3
    return max(failing)


# ── GitHub ──────────────────────────────────────────────────────────────────


def _github(method: str, url: str, token: str, body: dict | None = None) -> object:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = response.read()
    return json.loads(payload) if payload else None


def upsert_comment(body: str) -> None:
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    event_path = os.environ.get("GITHUB_EVENT_PATH", "")
    if not (token and repo and event_path):
        _say("::notice::No token or event to comment with; skipping the PR comment.")
        return
    event = json.loads(Path(event_path).read_text())
    number = (event.get("pull_request") or {}).get("number")
    if not number:
        return
    comments_url = f"{api}/repos/{repo}/issues/{number}/comments"
    try:
        existing = None
        page = 1
        while existing is None:
            batch = _github("GET", f"{comments_url}?per_page=100&page={page}", token)
            if not batch:
                break
            existing = next(
                (c for c in batch if COMMENT_MARKER in (c.get("body") or "")), None
            )
            page += 1
        if existing:
            _github("PATCH", existing["url"], token, {"body": body})
        else:
            _github("POST", comments_url, token, {"body": body})
    except urllib.error.HTTPError as exc:
        # A comment is a convenience; the job summary and the exit code are the
        # record. A fork's read-only token lands here.
        _say(f"::warning::Could not post the PR comment ({exc.code}): {exc.reason}")


# ── agent CLIs ──────────────────────────────────────────────────────────────


def backends(model: str, judge_model: str) -> list[str]:
    """The backends a run with these flags spawns, resolved as ``caliper run``
    resolves them: the judge follows ``--model``'s backend unless named
    (docs/adr/0034), and a bare model name is a claude-code model."""
    backend = DEFAULT_BACKEND
    if model:
        backend = parse_target(model)[0] or DEFAULT_BACKEND
    judge = backend
    if judge_model:
        judge = parse_target(judge_model)[0] or DEFAULT_BACKEND
    return sorted({backend, judge})


def codex_warmup(model: str, judge_model: str) -> int:
    """Refresh a ChatGPT-plan Codex login once, before attempts copy it.

    Caliper copies ``auth.json`` into every attempt's isolated home. A token
    due a refresh would otherwise be refreshed by every parallel attempt from
    the same stale copy — and the copies are thrown away, so the runner's own
    file never learns the new token. One cheap call refreshes it in place.
    An API-key login has nothing to refresh and is left alone.
    """
    if "codex" not in backends(model, judge_model):
        return 0
    # The file caliper copies into attempts (caliper/harness/codex.py), which
    # is ~/.codex whatever CODEX_HOME says; the warm-up refreshes that one.
    home = Path.home() / ".codex"
    try:
        auth = json.loads((home / "auth.json").read_text())
    except (OSError, ValueError):
        return 0
    if not auth.get("tokens"):
        return 0
    try:
        done = subprocess.run(
            [
                "codex",
                "exec",
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "Reply with the single word OK.",
            ],
            capture_output=True,
            timeout=180,
            check=False,
            env={**os.environ, "CODEX_HOME": str(home)},
        )
        ok = done.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        ok = False
    if not ok:
        _say("::warning::Codex warm-up failed; caliper run will diagnose the login.")
    return 0


# ── entry point ─────────────────────────────────────────────────────────────


def cli(argv: list[str]) -> int:
    command = argv[0] if argv else "run"
    if command == "backends":
        model, judge = (argv[1:] + ["", ""])[:2]
        sys.stdout.write(" ".join(backends(model, judge)) + "\n")
        return 0
    if command == "codex-warmup":
        model, judge = (argv[1:] + ["", ""])[:2]
        return codex_warmup(model, judge)
    if command == "run":
        return main()
    _say(f"unknown command {command!r}: expected run, backends or codex-warmup")
    return 2


def main() -> int:
    config = Config.from_env()
    root = Path.cwd()
    candidates = find_specs(config.specs, root)
    if not candidates:
        _say(f"::warning::No spec matches {' '.join(config.specs)}.")
    changed = None
    # A push to the default branch diffs against the commit before it, so the
    # base cache is refreshed for exactly the specs that push could move.
    if config.changed_only and (
        config.is_pull_request or config.is_default_branch_push
    ):
        changed = changed_files(config.base_sha)
        if changed is None:
            _say("::warning::Could not diff against the base; running every spec.")
    specs, notes = select_specs(candidates, changed)
    skipped = len(candidates) - len(specs)
    if skipped:
        notes.append(f"{skipped} spec(s) skipped: nothing they read changed.")

    planned = planned_attempts(specs, config.k)
    if config.max_attempts and planned > config.max_attempts:
        body = (
            f"{COMMENT_MARKER}\n## Caliper\n\nRefused before spending anything: "
            f"{len(specs)} spec(s) × k={config.k} is {planned} attempts, over "
            f"`max-attempts: {config.max_attempts}`. Lower `k`, narrow `specs`, "
            "or raise the ceiling.\n"
        )
        _publish(body, config)
        _say(f"::error::{planned} planned attempts exceed max-attempts.")
        return 1

    HEAD_DIR.mkdir(parents=True, exist_ok=True)
    runs = [run_spec(path, config) for path in specs]
    body = render(runs, notes, compare=config.is_pull_request)
    _publish(body, config)

    if config.is_default_branch_push:
        # Refresh the base a later PR compares with: only clean runs, so a
        # broken pipeline never becomes the baseline.
        BASE_DIR.mkdir(parents=True, exist_ok=True)
        for spec_run in runs:
            if spec_run.head is not None and spec_run.code in (0, 3):
                (BASE_DIR / spec_run.head.name).write_bytes(spec_run.head.read_bytes())

    return overall_code([r.code for r in runs])


def _publish(body: str, config: Config) -> None:
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(body.replace(COMMENT_MARKER, "") + "\n")
    else:
        sys.stdout.write(body)
    if config.is_pull_request and config.comment:
        upsert_comment(body)


if __name__ == "__main__":
    sys.exit(cli(sys.argv[1:]))

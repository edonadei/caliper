"""The GitHub Action's driver (caliper/ci.py)."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path, PurePosixPath

import pytest
import yaml

from caliper import ci

ROOT = Path(__file__).resolve().parent.parent


def _write_spec(path: Path, tasks: int = 1, skills: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(
        f"  - name: T{i}\n    prompt: Do it\n    assert: assert True\n"
        for i in range(tasks)
    )
    path.write_text(f"{skills}tasks:\n{body}")
    return path


def test_finds_specs_but_not_inside_caliper_or_vendored_dirs(tmp_path) -> None:
    _write_spec(tmp_path / "evals/a.eval.yaml")
    _write_spec(tmp_path / "node_modules/x/b.eval.yaml")
    _write_spec(tmp_path / ".caliper/results/c.eval.yaml")
    assert ci.find_specs(["**/*.eval.yaml"], tmp_path) == [Path("evals/a.eval.yaml")]


def test_a_spec_reads_its_own_dir_and_its_path_skills(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    spec = _write_spec(
        Path("evals/grill/grill.eval.yaml"),
        skills="skills:\n  - ../../skills/grill/SKILL.md\n",
    )
    inputs = ci.spec_inputs(spec, ci.load_spec(spec))
    assert inputs == [PurePosixPath("evals/grill"), PurePosixPath("skills/grill")]


@pytest.mark.parametrize(
    ("changed", "expected"),
    [
        (["skills/grill/SKILL.md"], True),
        (["skills/grill/references/a.md"], True),
        (["evals/grill/grill.eval.yaml"], True),
        (["skills/grilling/SKILL.md"], False),  # a sibling, not a child
        (["README.md"], False),
    ],
)
def test_touches_matches_whole_path_segments(changed, expected) -> None:
    inputs = [PurePosixPath("evals/grill"), PurePosixPath("skills/grill")]
    assert ci.touches(changed, inputs) is expected


def test_unknown_diff_runs_every_spec(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    a = _write_spec(Path("a/a.eval.yaml"))
    b = _write_spec(Path("b/b.eval.yaml"))
    assert ci.select_specs([a, b], None)[0] == [a, b]
    assert ci.select_specs([a, b], ["b/fixture.txt"])[0] == [b]


def test_a_broken_spec_still_runs_so_caliper_can_diagnose_it(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    bad = Path("bad.eval.yaml")
    bad.write_text("tasks: nope\n")
    selected, notes = ci.select_specs([bad], [])
    assert selected == [bad]
    assert "does not load" in notes[0]


def test_planned_attempts_is_tasks_times_k(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    specs = [_write_spec(Path("a.eval.yaml"), 2), _write_spec(Path("b.eval.yaml"), 3)]
    assert ci.planned_attempts(specs, k=4) == 20


@pytest.mark.parametrize(
    ("codes", "expected"),
    [([0, 0], 0), ([0, 2], 2), ([2, 3], 3), ([1, 0], 1), ([], 0)],
)
def test_a_missed_bar_is_the_jobs_verdict(codes, expected) -> None:
    assert ci.overall_code(codes) == expected


@pytest.mark.parametrize(
    ("model", "judge", "expected"),
    [
        ("", "", ["claude-code"]),
        ("codex", "", ["codex"]),
        ("codex:gpt-5-codex", "claude-code", ["claude-code", "codex"]),
        ("claude-sonnet-4-6", "", ["claude-code"]),
    ],
)
def test_backends_resolve_like_caliper_run(model, judge, expected) -> None:
    assert ci.backends(model, judge) == expected


def test_warmup_leaves_an_api_key_login_alone(tmp_path, monkeypatch) -> None:
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex/auth.json").write_text(json.dumps({"OPENAI_API_KEY": "sk"}))
    monkeypatch.setattr(ci.Path, "home", lambda: tmp_path)
    spawned = []
    monkeypatch.setattr(ci.subprocess, "run", lambda *a, **k: spawned.append(a))
    assert ci.codex_warmup("codex", "") == 0
    assert spawned == []


def test_warmup_refreshes_a_chatgpt_login(tmp_path, monkeypatch) -> None:
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex/auth.json").write_text(json.dumps({"tokens": {"id": "x"}}))
    monkeypatch.setattr(ci.Path, "home", lambda: tmp_path)
    spawned = []

    class Done:
        returncode = 0

    monkeypatch.setattr(
        ci.subprocess, "run", lambda cmd, **k: spawned.append(cmd) or Done()
    )
    ci.codex_warmup("codex", "")
    assert spawned[0][:2] == ["codex", "exec"]


def test_render_says_why_a_run_left_nothing(tmp_path) -> None:
    run = ci.SpecRun(path=Path("evals/a.eval.yaml"), code=2, head=None, base=None)
    body = ci.render([run], [], compare=True)
    assert body.startswith(ci.COMMENT_MARKER)
    assert "exit 2" in body
    assert "could not run" in body


def test_render_compares_with_the_base_when_there_is_one(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        ci, "_caliper_markdown", lambda *args: calls.append(args) or "TABLE\n"
    )
    run = ci.SpecRun(
        path=Path("a.eval.yaml"),
        code=0,
        head=Path("head/a.json"),
        base=Path("base/a.json"),
        bar_note="moved the bar",
    )
    body = ci.render([run], [], compare=True)
    assert calls == [
        ("report", "head/a.json"),
        ("compare", "base/a.json", "head/a.json"),
    ]
    assert "moved the bar" in body
    assert "<details>" in body


def _env(monkeypatch, tmp_path, **values) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))
    for key in ("GITHUB_EVENT_NAME", "GITHUB_REF", "CALIPER_DEFAULT_BRANCH"):
        monkeypatch.delenv(key, raising=False)
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def test_the_ceiling_refuses_before_anything_runs(tmp_path, monkeypatch) -> None:
    _write_spec(tmp_path / "a.eval.yaml", tasks=5)
    _env(monkeypatch, tmp_path, CALIPER_K="5", CALIPER_MAX_ATTEMPTS="10")
    monkeypatch.setattr(ci, "run_spec", lambda *a: pytest.fail("ran"))

    assert ci.main() == 1
    assert "25 attempts" in (tmp_path / "summary.md").read_text()


def test_a_push_to_the_default_branch_refreshes_the_base(tmp_path, monkeypatch) -> None:
    _write_spec(tmp_path / "a.eval.yaml")
    _write_spec(tmp_path / "b.eval.yaml")
    _env(
        monkeypatch,
        tmp_path,
        GITHUB_EVENT_NAME="push",
        GITHUB_REF="refs/heads/main",
        CALIPER_DEFAULT_BRANCH="main",
        CALIPER_CHANGED_ONLY="false",
    )
    monkeypatch.setattr(ci, "_caliper_markdown", lambda *a: "TABLE\n")

    def fake_run(path, config):
        head = ci.HEAD_DIR / f"{ci.spec_name(path)}.json"
        head.write_text("{}")
        # b could not run: a broken pipeline must not become the baseline.
        code = 2 if path.name.startswith("b") else 0
        return ci.SpecRun(path=path, code=code, head=head, base=None)

    monkeypatch.setattr(ci, "run_spec", fake_run)

    assert ci.main() == 2
    assert sorted(p.name for p in ci.BASE_DIR.iterdir()) == ["a.json"]
    # Filed where the sandbox forbids saved results, and not listed as a spec.
    from caliper.runstore import RunStore
    from caliper.sandbox import SAVED_RESULTS

    assert re.search(SAVED_RESULTS, str(ci.BASE_DIR / "a.json"))
    assert RunStore.discover().specs() == []


def test_action_inputs_reach_the_driver() -> None:
    """Every CALIPER_* variable the driver reads is set by action.yml."""
    action = yaml.safe_load((ROOT / "action.yml").read_text())
    run_step = next(s for s in action["runs"]["steps"] if s.get("id") == "run")
    source = (ROOT / "caliper/ci.py").read_text()
    read = set(re.findall(r'"(CALIPER_[A-Z_]+)"', source))
    assert read <= set(run_step["env"]), read - set(run_step["env"])


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


def test_diff_and_bar_change_read_the_base_commit(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _git(tmp_path, "init", "-q")
    spec = _write_spec(Path("evals/a.eval.yaml"))
    spec.write_text("bar:\n  score: 0.9\n" + spec.read_text())
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "base")
    base = _git(tmp_path, "rev-parse", "HEAD")

    assert ci.bar_change(spec, base) is None
    spec.write_text(spec.read_text().replace("0.9", "0.5"))
    Path("evals/fixture.txt").write_text("x")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "head")

    assert sorted(ci.changed_files(base)) == ["evals/a.eval.yaml", "evals/fixture.txt"]
    note = ci.bar_change(spec, base)
    assert "0.9" in note
    assert "0.5" in note

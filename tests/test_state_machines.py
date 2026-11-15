"""Caliper's state machines, run as Hypothesis rule-based machines.

docs/statecharts.md draws each one. The machines here walk the same states,
and the last tests check that the charts still name them.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from conftest import ScriptedHarness, ScriptedJudge
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from hypothesis.stateful import (
    Bundle,
    RuleBasedStateMachine,
    consumes,
    initialize,
    invariant,
    rule,
)

from caliper import cancel
from caliper.activation import ActivationDetector
from caliper.attempt import SetupFailed, assemble_attempt
from caliper.compare import diff_runs
from caliper.harness.base import (
    AttemptResult,
    ConversationTurn,
    HarnessConfigurationError,
    RunContext,
)
from caliper.harness.refusal import CliRefusal, RefusalKind
from caliper.judge.base import JudgeResult
from caliper.reporter import comparison_to_json
from caliper.runner import RunAborted, run
from caliper.schema.results import (
    AggregateScore,
    AttemptRecord,
    Outcome,
    RunMeta,
    RunResults,
    TaskResult,
    TokenUsage,
    TranscriptTurn,
)
from caliper.schema.spec import EvalSpec, TaskSpec
from caliper.workdir import AttemptWorkdir

ROOT = Path(__file__).resolve().parent.parent
STATECHARTS = ROOT / "docs" / "statecharts.md"


def _state_name(outcome: Outcome) -> str:
    """``task_fail`` as the charts spell it: ``TaskFail``."""
    return "".join(part.title() for part in outcome.value.split("_"))


# --- the attempt verdict ---------------------------------------------------

SKILL = "my-skill"

# What can be true of one attempt, in the order caliper/attempt.py tests it,
# and the state each one ends in when it is the first that holds.
VERDICT_PRECEDENCE = (
    ("setup_failed", "InfraError"),
    ("cancelled", "Discarded"),
    ("timed_out", "Timeout"),
    ("exit_nonzero", "InfraError"),
    ("refused", "InfraError"),
    ("no_model_call", "InfraError"),
    ("forbidden_read", "Cheat"),
    ("unchecked", "NotChecked"),
)
JUDGE_VERDICTS = {"pass": "Pass", "fail": "TaskFail", "error": "JudgeError"}
# Exits that skip the judge because the agent never got a fair shot, so
# activation is observed but not graded (docs/CONTEXT.md → Activation
# admissibility).
_INADMISSIBLE = {"timed_out", "exit_nonzero", "refused", "no_model_call"}


class _CountingJudge:
    backend = "test"
    model = None
    effort = None
    prompt_version = None

    def __init__(self, verdict: str) -> None:
        self.result = JudgeResult(
            passed=verdict == "pass",
            errored=verdict == "error",
            autorater_seconds=0.5,
        )
        self.calls = 0

    def evaluate(self, task, transcript, workdir, history=None) -> JudgeResult:
        self.calls += 1
        return self.result


class _StubSandbox:
    def __init__(self, found: list[str]) -> None:
        self.found = found

    def violations(self, transcript) -> list[str]:
        return list(self.found)


class AttemptVerdictMachine(RuleBasedStateMachine):
    """Facts about one attempt pile up in any order; the verdict follows the chart.

    Several facts can hold at once (a run that timed out *and* read a forbidden
    file), which is what makes the precedence worth checking at every step.
    """

    def __init__(self) -> None:
        super().__init__()
        self.facts: set[str] = set()
        self.verdict = "pass"
        self.loaded = False

    @rule(fact=st.sampled_from([fact for fact, _ in VERDICT_PRECEDENCE]))
    def happen(self, fact: str) -> None:
        self.facts.add(fact)

    @rule(verdict=st.sampled_from(sorted(JUDGE_VERDICTS)))
    def judge_answers(self, verdict: str) -> None:
        self.verdict = verdict

    @rule()
    def skill_loads(self) -> None:
        self.loaded = True

    def _ended(self) -> AttemptResult | SetupFailed:
        if "setup_failed" in self.facts:
            return SetupFailed("setup exited 7")
        transcript = [ConversationTurn(role="assistant", content="done")]
        if self.loaded:
            transcript.insert(
                0,
                ConversationTurn(
                    role="tool_use",
                    content="[tool: Skill]",
                    tool_name="Skill",
                    tool_input={"skill": SKILL},
                ),
            )
        silent = "no_model_call" in self.facts
        return AttemptResult(
            transcript=[] if silent else transcript,
            final_output="" if silent else "done",
            exit_code=1 if "exit_nonzero" in self.facts else 0,
            duration_seconds=1.0,
            timed_out="timed_out" in self.facts,
            usage=None if silent else TokenUsage(input_tokens=10, output_tokens=2),
            cancelled="cancelled" in self.facts,
            refusal=CliRefusal(RefusalKind.THROTTLE, "rate limited")
            if "refused" in self.facts
            else None,
        )

    def _expected(self) -> str:
        for fact, state in VERDICT_PRECEDENCE:
            if fact in self.facts:
                return state
        return JUDGE_VERDICTS[self.verdict]

    @invariant()
    def ends_where_the_chart_says(self) -> None:
        judge = _CountingJudge(self.verdict)
        unchecked = "unchecked" in self.facts
        assembled = assemble_attempt(
            self._ended(),
            attempt=1,
            task=TaskSpec(
                id="task-001",
                name="t",
                prompt="go",
                expect=None if unchecked else "it works",
                activates=[SKILL],
            ),
            # Never entered: the counting judge runs nothing in it.
            workdir=AttemptWorkdir("/tmp"),
            expected_activation=[SKILL],
            activation=ActivationDetector([SKILL], frozenset({"Skill"})),
            sandbox=_StubSandbox(["/secret"] if "forbidden_read" in self.facts else []),
            judge=judge,
        )
        expected = self._expected()

        judged = expected in JUDGE_VERDICTS.values()
        assert judge.calls == (1 if judged else 0)
        if expected == "Discarded":
            assert assembled is None
            return
        assert assembled is not None
        record = assembled.record
        assert _state_name(record.outcome) == expected
        assert (record.judge_seconds is not None) == judged

        saw_load = self.loaded and "no_model_call" not in self.facts
        first = next((f for f, _ in VERDICT_PRECEDENCE if f in self.facts), None)
        if first == "setup_failed":
            assert record.activated is None
            assert record.activation_passed is None
        elif first in _INADMISSIBLE:
            # A load seen before the cut still happened; seeing none is not a
            # negative observation.
            assert record.activated == ([SKILL] if saw_load else None)
            assert record.activation_passed is None
        else:
            assert record.activated == ([SKILL] if self.loaded else [])
            assert record.activation_passed is self.loaded


TestAttemptVerdict = AttemptVerdictMachine.TestCase
TestAttemptVerdict.settings = settings(max_examples=150, stateful_step_count=12)


# --- a tracked process -----------------------------------------------------

# Waits on stdin, then exits with the code it reads: a process that only ends
# when the machine says so, either on its own or killed.
_AGENT = "import sys; sys.exit(int(sys.stdin.read() or 0))"
PROCESS_STATES = ("Live", "Finished", "Killed")


class TrackedProcessMachine(RuleBasedStateMachine):
    """Real processes registered with caliper/cancel.py, finished and killed.

    ``was_killed`` must name exactly the processes the module killed while they
    ran, because that is what turns an attempt into no record at all; one that
    exited on its own stays an observation. Leaving ``track`` must forget it.
    """

    procs = Bundle("procs")

    def __init__(self) -> None:
        super().__init__()
        cancel.reset()
        self.state: dict[subprocess.Popen, str] = {}
        self.tracking: dict[subprocess.Popen, contextlib.ExitStack] = {}
        self.tags: dict[subprocess.Popen, str] = {}
        self.stop_requested = False

    @rule(target=procs, tagged=st.booleans(), cleanup=st.booleans())
    def spawn(self, tagged: bool, cleanup: bool) -> subprocess.Popen:
        proc = subprocess.Popen(
            [sys.executable, "-c", _AGENT],
            stdin=subprocess.PIPE,
            start_new_session=True,
        )
        tag = f"caliper-state-machine-{proc.pid}" if tagged else None
        stack = contextlib.ExitStack()
        # A cleanup hook skips the immediate kill, so it still runs after a stop.
        stack.enter_context(
            cancel.track(proc, cancel_if_requested=not cleanup, process_tag=tag)
        )
        self.tracking[proc] = stack
        if tag is not None:
            self.tags[proc] = tag
        killed_on_entry = self.stop_requested and not cleanup
        self.state[proc] = "Killed" if killed_on_entry else "Live"
        if killed_on_entry:
            proc.wait(timeout=10)
        return proc

    @rule(proc=procs, code=st.integers(0, 3))
    def exits_on_its_own(self, proc: subprocess.Popen, code: int) -> None:
        if self.state[proc] != "Live":
            return
        proc.communicate(str(code).encode(), timeout=10)
        assert proc.returncode == code
        self.state[proc] = "Finished"

    @rule(proc=procs)
    def times_out(self, proc: subprocess.Popen) -> None:
        cancel.kill(proc)
        if self.state[proc] == "Live":
            self.state[proc] = "Killed"
            proc.wait(timeout=10)

    @rule()
    def stop_is_requested(self) -> None:
        cancel.request()
        self.stop_requested = True
        for proc in self.tracking:
            if self.state[proc] == "Live":
                self.state[proc] = "Killed"
                proc.wait(timeout=10)

    @rule(proc=consumes(procs))
    def leaves_track(self, proc: subprocess.Popen) -> None:
        self.tracking.pop(proc).close()
        self.tags.pop(proc, None)

    @invariant()
    def was_killed_names_only_what_the_module_killed(self) -> None:
        for proc, state in self.state.items():
            assert cancel.was_killed(proc) == (state == "Killed")

    @invariant()
    def only_tracked_processes_are_registered(self) -> None:
        assert set(cancel._live) == set(self.tracking)
        assert set(cancel._descendants) == set(self.tracking)
        assert cancel._tags == self.tags
        assert cancel.requested() == self.stop_requested

    def teardown(self) -> None:
        for stack in self.tracking.values():
            stack.close()
        try:
            assert not cancel._live
            assert not cancel._descendants
            assert not cancel._tags
        finally:
            for proc in self.state:
                if proc.poll() is None:
                    proc.kill()
                proc.wait(timeout=10)
                if proc.stdin is not None:
                    proc.stdin.close()
            cancel.reset()


TestTrackedProcess = TrackedProcessMachine.TestCase
TestTrackedProcess.settings = settings(
    max_examples=40, stateful_step_count=10, deadline=None
)


# --- a run that stops ------------------------------------------------------

# How one attempt ends, as the harness reports it. The first three leave the
# run going; the rest stop it.
_ACTIONS = (
    "passes",
    "fails",
    "times_out",
    "killed",
    "fails_while_stopping",
    "misconfigured",
    "capped",
)
_STOPS = {"killed", "fails_while_stopping", "misconfigured", "capped"}
_FATAL = {"misconfigured", "capped"}
_KEPT = {
    "passes": Outcome.PASS,
    "fails": Outcome.INFRA_ERROR,
    "times_out": Outcome.TIMEOUT,
    "fails_while_stopping": Outcome.INFRA_ERROR,
}


def _scripted(actions: list[str]):
    def answer(ctx: RunContext) -> AttemptResult:
        action = actions[ctx.attempt - 1]
        if action == "misconfigured":
            raise HarnessConfigurationError("credentials expired mid-run")
        if action in ("killed", "fails_while_stopping"):
            cancel.request()
        return AttemptResult(
            transcript=[ConversationTurn(role="assistant", content="done")],
            final_output="done",
            exit_code=1 if action in ("fails", "fails_while_stopping", "killed") else 0,
            duration_seconds=0.01,
            timed_out=action == "times_out",
            cancelled=action == "killed",
            refusal=CliRefusal(RefusalKind.SPENDING_CAP, "spending cap reached")
            if action == "capped"
            else None,
        )

    return answer


@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(actions=st.lists(st.sampled_from(_ACTIONS), min_size=1, max_size=6))
def test_a_stopped_run_keeps_what_failed_on_its_own_and_drops_what_it_killed(
    tmp_path, actions
):
    spec_path = tmp_path / "sample.eval.yaml"
    spec_path.write_text("tasks: []\n")
    harness = ScriptedHarness(_scripted(actions))
    judge = ScriptedJudge()

    stop = next((i for i, a in enumerate(actions) if a in _STOPS), None)
    ran = actions if stop is None else actions[: stop + 1]
    try:
        results = run(
            spec=EvalSpec(
                tasks=[
                    TaskSpec(id="task-001", name="t", prompt="go", expect="it works")
                ]
            ),
            spec_path=spec_path,
            harness=harness,
            judge=judge,
            k=len(actions),
            workers=1,
            timeout=5,
        )
        assert not _FATAL.intersection(ran)
    except RunAborted as aborted:
        assert ran[-1] in _FATAL
        results = aborted.results
    finally:
        cancel.reset()

    # Nothing after the stop starts; what ran on its own is kept, in order.
    assert [ctx.attempt for ctx in harness.contexts] == list(range(1, len(ran) + 1))
    kept = [(i, _KEPT[a]) for i, a in enumerate(ran, start=1) if a in _KEPT]
    attempts = results.task_results[0].attempts
    assert [(a.attempt, a.outcome) for a in attempts] == kept
    assert results.run.interrupted == (stop is not None)
    assert judge.calls == sum(1 for _, outcome in kept if outcome is Outcome.PASS)


# --- the hillclimb held-out boundary ---------------------------------------

_spec = importlib.util.spec_from_file_location(
    "climb", ROOT / "skills/hillclimb-skill/climb.py"
)
climb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(climb)

CLIMB_STATES = ("Unsplit", "Split")
_CLIMB_OUTCOMES = (
    Outcome.PASS,
    Outcome.TASK_FAIL,
    Outcome.CHEAT,
    Outcome.INFRA_ERROR,
    Outcome.NOT_CHECKED,
)


def _marker(name: str) -> str:
    return f"<<only-in-{name}>>"


@st.composite
def _climb_runs(draw, names: list[str]) -> RunResults:
    tasks = []
    for name in names:
        k = draw(st.integers(1, 3))
        outcomes = draw(
            st.lists(st.sampled_from(_CLIMB_OUTCOMES), min_size=k, max_size=k)
        )
        # At least one attempt each surface can measure.
        outcomes[0] = draw(st.sampled_from((Outcome.PASS, Outcome.TASK_FAIL)))
        secret = _marker(name)
        tasks.append(
            TaskResult(
                task_id=name,
                task_name=name,
                prompt=f"prompt {secret}",
                expect=f"expect {secret}",
                activation_expected=draw(st.sampled_from(([SKILL], []))),
                attempts=[
                    AttemptRecord(
                        attempt=i,
                        output=f"output {secret}",
                        duration_seconds=1.0,
                        outcome=outcome,
                        activated=draw(st.sampled_from(([SKILL], [], None))),
                        activation_passed=draw(st.sampled_from((True, False, None))),
                        autorater_reasoning=f"reasoning {secret}",
                        assert_evidence=f"evidence {secret}",
                        cheat_evidence=[f"/read/{secret}"]
                        if outcome is Outcome.CHEAT
                        else [],
                        transcript=[
                            TranscriptTurn(role="user", content=f"turn {secret}"),
                            TranscriptTurn(
                                role="tool_use",
                                content="[tool: Read]",
                                tool_name="Read",
                                tool_input={"file_path": secret},
                                tool_output=f"read {secret}",
                            ),
                        ],
                    )
                    for i, outcome in enumerate(outcomes, start=1)
                ],
            )
        )
    return RunResults(
        run=RunMeta(
            spec="demo",
            timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            k=3,
            backend="claude-code",
        ),
        task_results=tasks,
        aggregate=AggregateScore.from_task_results(tasks, k=3),
    )


def _climb(*argv: str) -> str:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        climb.main(list(argv))
    return out.getvalue()


class HeldOutBoundaryMachine(RuleBasedStateMachine):
    """A climb: one split, then any mix of reruns, failure reads and score reads.

    Whatever the climber asks for, no held-out task's prompt, expect, verdict
    or transcript reaches it; a held-out task shows only as a share of a score.
    """

    def __init__(self) -> None:
        super().__init__()
        self.dir = Path(tempfile.mkdtemp())
        self.run_path = self.dir / "run.json"
        self.state_path = self.dir / "hillclimb" / "state.json"
        self.phase = "Unsplit"

    def teardown(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)

    def _save(self, results: RunResults) -> None:
        self.run_path.write_text(results.model_dump_json(), encoding="utf-8")
        self.results = results

    @initialize(
        data=st.data(),
        count=st.integers(climb.MIN_TASKS, 14),
        surface=st.sampled_from(("body", "description")),
        seed=st.integers(0, 10**6),
    )
    def split(self, data, count: int, surface: str, seed: int) -> None:
        self.names = [f"task-{i:02d}" for i in range(count)]
        self._save(data.draw(_climb_runs(self.names)))
        argv = ["split", str(self.run_path), str(self.state_path)]
        argv += ["--surface", surface, "--seed", str(seed)]
        if surface == "description":
            argv += ["--skill", SKILL]
        _climb(*argv)
        self.phase = "Split"
        self.saved_state = self.state_path.read_text(encoding="utf-8")
        state = json.loads(self.saved_state)
        self.train = set(state["train"])
        self.held_out = set(state["held_out"])

    @rule(data=st.data())
    def reruns(self, data) -> None:
        self._save(data.draw(_climb_runs(self.names)))

    @rule(data=st.data(), one=st.booleans())
    def reads_failures(self, data, one: bool) -> None:
        argv = ["failures", str(self.run_path), str(self.state_path)]
        if not one:
            self._assert_hidden(_climb(*argv))
            return
        task = data.draw(st.sampled_from(self.names))
        if task in self.held_out:
            with pytest.raises(SystemExit, match="not a train task"):
                _climb(*argv, "--task", task)
            return
        out = _climb(*argv, "--task", task)
        self._assert_hidden(out)
        assert not any(f"## {name} " in out for name in self.train - {task})

    @rule(data=st.data())
    def reads_scores(self, data) -> None:
        after = data.draw(_climb_runs(self.names))
        comparison = comparison_to_json(diff_runs(self.results, after))
        stdin = sys.stdin
        sys.stdin = io.StringIO(comparison)
        try:
            self._assert_hidden(_climb("scores", str(self.state_path)))
        finally:
            sys.stdin = stdin

    @rule()
    def splits_again(self) -> None:
        with pytest.raises(SystemExit, match="fixed for the whole climb"):
            _climb(
                "split", str(self.run_path), str(self.state_path), "--surface", "body"
            )

    def _assert_hidden(self, out: str) -> None:
        for name in self.held_out:
            assert name not in out
            assert _marker(name) not in out

    @invariant()
    def the_split_never_moves(self) -> None:
        if self.phase == "Unsplit":
            return
        assert self.state_path.read_text(encoding="utf-8") == self.saved_state
        assert not self.train & self.held_out
        assert self.train | self.held_out == set(self.names)


TestHeldOutBoundary = HeldOutBoundaryMachine.TestCase
TestHeldOutBoundary.settings = settings(
    max_examples=30, stateful_step_count=8, deadline=None
)


# --- the charts ------------------------------------------------------------

_EDGE = re.compile(r"^\s*(\[\*\]|\w+)\s*-->\s*(\[\*\]|\w+)", re.MULTILINE)


def _charts() -> list[list[tuple[str, str]]]:
    """Each ```mermaid block's edges, in the order docs/statecharts.md has them."""
    blocks = re.findall(r"```mermaid\n(.*?)```", STATECHARTS.read_text(), re.DOTALL)
    return [_EDGE.findall(block) for block in blocks]


def _states(edges: list[tuple[str, str]]) -> set[str]:
    return {state for edge in edges for state in edge} - {"[*]"}


def _terminal(edges: list[tuple[str, str]]) -> set[str]:
    leaving = {source for source, target in edges if target != "[*]"}
    return _states(edges) - leaving


def test_the_verdict_chart_ends_in_every_outcome_and_nothing_else():
    verdict, _, _ = _charts()
    reached = {state for _, state in VERDICT_PRECEDENCE} | set(JUDGE_VERDICTS.values())

    assert {_state_name(o) for o in Outcome} | {"Discarded"} == reached
    # The runner's own exit: a spending cap or misconfiguration stops the run.
    assert _terminal(verdict) == reached | {"RunAborted"}


def test_the_process_and_climb_charts_name_the_machines_states():
    _, process, hillclimb = _charts()

    assert _states(process) == set(PROCESS_STATES)
    assert _states(hillclimb) == set(CLIMB_STATES)

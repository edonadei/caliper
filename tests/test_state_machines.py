"""Caliper's state machines, run as Hypothesis rule-based machines.

docs/statecharts.md draws each one. The machines here move along the same
edges, and the last tests check that the charts still draw exactly those.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple

import psutil
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

Edge = tuple[str, str]


def _state_name(outcome: Outcome) -> str:
    """``task_fail`` as the charts spell it: ``TaskFail``."""
    return "".join(part.title() for part in outcome.value.split("_"))


# --- the attempt verdict ---------------------------------------------------

SKILL = "my-skill"

# What can be true of one attempt, in the order caliper/attempt.py tests it,
# and the edge the attempt leaves by when it is the first that holds.
_VERDICT_PRECEDENCE: tuple[tuple[str, Edge], ...] = (
    ("setup_failed", ("Setup", "InfraError")),
    ("cancelled", ("Agent", "Discarded")),
    ("timed_out", ("Agent", "Timeout")),
    ("exit_nonzero", ("Agent", "InfraError")),
    ("refused", ("Agent", "InfraError")),
    ("no_model_call", ("Agent", "InfraError")),
    ("forbidden_read", ("Sandbox", "Cheat")),
    ("unchecked", ("Sandbox", "NotChecked")),
)
_JUDGE_VERDICTS = {"pass": "Pass", "fail": "TaskFail", "error": "JudgeError"}
# The way through when no exit above holds.
_VERDICT_PATH: tuple[Edge, ...] = (
    ("[*]", "Setup"),
    ("Setup", "Agent"),
    ("Agent", "Sandbox"),
    ("Sandbox", "Judge"),
)
# Decided around attempt.py rather than in it, each checked elsewhere: the
# throttle retry in tests/test_retry.py, a stop around the agent in
# tests/test_cancel.py, and a fatal refusal by the stopped-run property below.
_RUNNER_EDGES: tuple[Edge, ...] = (
    ("Setup", "Discarded"),
    ("Agent", "Agent"),
    ("Agent", "RunAborted"),
)
# Exits that skip the judge because the agent never got a fair shot, so
# activation is observed but not graded (docs/CONTEXT.md → Activation
# admissibility).
_INADMISSIBLE = {"timed_out", "exit_nonzero", "refused", "no_model_call"}


def _verdict_edges() -> set[Edge]:
    exits = {edge for _, edge in _VERDICT_PRECEDENCE}
    judged = {("Judge", state) for state in _JUDGE_VERDICTS.values()}
    return exits | judged | set(_VERDICT_PATH) | set(_RUNNER_EDGES)


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

    @rule(fact=st.sampled_from([fact for fact, _ in _VERDICT_PRECEDENCE]))
    def happen(self, fact: str) -> None:
        self.facts.add(fact)

    @rule(verdict=st.sampled_from(sorted(_JUDGE_VERDICTS)))
    def judge_answers(self, verdict: str) -> None:
        self.verdict = verdict

    @rule()
    def skill_loads(self) -> None:
        self.loaded = True

    def _harness_result(self) -> AttemptResult | SetupFailed:
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

    @invariant()
    def ends_where_the_chart_says(self) -> None:
        judge = ScriptedJudge(
            JudgeResult(
                passed=self.verdict == "pass",
                errored=self.verdict == "error",
                autorater_seconds=0.5,
            )
        )
        unchecked = "unchecked" in self.facts
        assembled = assemble_attempt(
            self._harness_result(),
            attempt=1,
            task=TaskSpec(
                id="task-001",
                name="t",
                prompt="go",
                expect=None if unchecked else "it works",
                activates=[SKILL],
            ),
            # Never entered: the scripted judge runs nothing in it.
            workdir=AttemptWorkdir(ROOT),
            expected_activation=[SKILL],
            activation=ActivationDetector([SKILL], frozenset({"Skill"})),
            sandbox=_StubSandbox(["/secret"] if "forbidden_read" in self.facts else []),
            judge=judge,
        )
        first_exit = next((f for f, _ in _VERDICT_PRECEDENCE if f in self.facts), None)
        if first_exit is None:
            expected = _JUDGE_VERDICTS[self.verdict]
        else:
            expected = dict(_VERDICT_PRECEDENCE)[first_exit][1]

        judged = first_exit is None
        assert judge.calls == (1 if judged else 0)
        if expected == "Discarded":
            assert assembled is None
            return
        assert assembled is not None
        record = assembled.record
        assert _state_name(record.outcome) == expected
        assert (record.judge_seconds is not None) == judged

        if first_exit == "setup_failed":
            assert record.activated is None
            assert record.activation_passed is None
        elif first_exit in _INADMISSIBLE:
            # A load seen before the cut still happened; seeing none is not a
            # negative observation.
            saw_load = self.loaded and "no_model_call" not in self.facts
            assert record.activated == ([SKILL] if saw_load else None)
            assert record.activation_passed is None
        else:
            assert record.activated == ([SKILL] if self.loaded else [])
            assert record.activation_passed is self.loaded


TestAttemptVerdict = AttemptVerdictMachine.TestCase
TestAttemptVerdict.settings = settings(max_examples=150, stateful_step_count=12)


# --- a tracked process -----------------------------------------------------

# Starts a tool in its own session, says which, then waits on stdin and exits
# with the code it reads: an agent that only ends when the machine says so, and
# leaves its tool behind, reparented, when it ends on its own.
_AGENT = (
    "import subprocess, sys; "
    "tool = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], "
    "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, start_new_session=True); "
    "print(tool.pid, flush=True); "
    "sys.exit(int(sys.stdin.read() or 0))"
)
_PROCESS_EDGES: tuple[Edge, ...] = (
    ("[*]", "Live"),
    ("[*]", "Killed"),
    ("Live", "Finished"),
    ("Live", "Killed"),
    ("Finished", "Finished"),
)


@dataclass
class _Agent:
    """One agent process, and what the machine expects of it and its tool."""

    proc: subprocess.Popen
    tool: psutil.Process
    tag: str | None
    tracking: contextlib.ExitStack | None
    state: str = "[*]"
    tool_alive: bool = True

    def move(self, to: str) -> None:
        assert (self.state, to) in _PROCESS_EDGES
        self.state = to


def _running(process: psutil.Process) -> bool:
    try:
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


class TrackedProcessMachine(RuleBasedStateMachine):
    """Real agents registered with caliper/cancel.py, finished and killed.

    ``was_killed`` must name exactly the agents the module killed while they
    ran: one that comes back after a stop killed it is no record at all, and one
    that exited on its own stays an observation. A kill must reach the agent's
    tool even after the agent is gone, and leaving ``track`` must forget it.
    """

    agents = Bundle("agents")

    def __init__(self) -> None:
        super().__init__()
        cancel.reset()
        self.all: list[_Agent] = []
        self.stop_requested = False

    def _tracked(self) -> list[_Agent]:
        return [agent for agent in self.all if agent.tracking is not None]

    @rule(target=agents, tagged=st.booleans(), cleanup=st.booleans())
    def spawn(self, tagged: bool, cleanup: bool) -> _Agent:
        tag = f"caliper-state-machine-{os.getpid()}-{len(self.all)}" if tagged else None
        env = dict(os.environ)
        if tag is not None:
            # As the real harness does: the tool inherits it.
            env[cancel.PROCESS_TAG] = tag
        proc = subprocess.Popen(
            [sys.executable, "-c", _AGENT],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            env=env,
            start_new_session=True,
        )
        assert proc.stdout is not None
        tool = psutil.Process(int(proc.stdout.readline()))
        tracking = contextlib.ExitStack()
        # A cleanup hook skips the immediate kill, so it still runs after a stop.
        tracking.enter_context(
            cancel.track(proc, cancel_if_requested=not cleanup, process_tag=tag)
        )
        agent = _Agent(proc, tool, tag, tracking)
        self.all.append(agent)
        if self.stop_requested and not cleanup:
            agent.move("Killed")
            agent.tool_alive = False
            proc.wait(timeout=10)
        else:
            agent.move("Live")
            self._until_the_watcher_sees(agent)
        return agent

    def _until_the_watcher_sees(self, agent: _Agent) -> None:
        # Before then, only the live agent leads to its tool.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with cancel._lock:
                if agent.tool in cancel._descendants.get(agent.proc, ()):
                    return
            time.sleep(0.01)
        raise AssertionError("the watcher never saw the tool")

    def _killed(self, agent: _Agent) -> None:
        """What one ``cancel.kill`` should have done to an agent and its tool."""
        if agent.state == "Live":
            agent.move("Killed")
            agent.tool_alive = False
            agent.proc.wait(timeout=10)
        elif agent.state == "Finished":
            agent.move("Finished")
            # Reparented: found through what tracking remembered, or the tag.
            if agent.tracking is not None or agent.tag is not None:
                agent.tool_alive = False

    @rule(agent=agents, code=st.integers(0, 3))
    def exits_on_its_own(self, agent: _Agent, code: int) -> None:
        if agent.state != "Live":
            return
        agent.proc.communicate(str(code).encode(), timeout=10)
        assert agent.proc.returncode == code
        agent.move("Finished")

    @rule(agent=agents)
    def times_out(self, agent: _Agent) -> None:
        cancel.kill(agent.proc)
        self._killed(agent)

    @rule()
    def stop_is_requested(self) -> None:
        cancel.request()
        self.stop_requested = True
        for agent in self._tracked():
            self._killed(agent)

    @rule(agent=consumes(agents))
    def leaves_track(self, agent: _Agent) -> None:
        assert agent.tracking is not None
        agent.tracking.close()
        agent.tracking = None

    @invariant()
    def was_killed_names_only_what_the_module_killed(self) -> None:
        for agent in self.all:
            assert cancel.was_killed(agent.proc) == (agent.state == "Killed")

    @invariant()
    def a_kill_reaches_the_tool(self) -> None:
        for agent in self.all:
            if not agent.tool_alive:
                psutil.wait_procs([agent.tool], timeout=5)
            assert _running(agent.tool) == agent.tool_alive

    @invariant()
    def only_tracked_agents_are_registered(self) -> None:
        tracked = self._tracked()
        assert set(cancel._live) == {agent.proc for agent in tracked}
        assert set(cancel._descendants) == {agent.proc for agent in tracked}
        assert cancel._tags == {a.proc: a.tag for a in tracked if a.tag is not None}
        assert cancel.requested() == self.stop_requested

    def teardown(self) -> None:
        for agent in self._tracked():
            assert agent.tracking is not None
            agent.tracking.close()
        try:
            assert not cancel._live
            assert not cancel._descendants
            assert not cancel._tags
        finally:
            for agent in self.all:
                with contextlib.suppress(psutil.NoSuchProcess):
                    agent.tool.kill()
                if agent.proc.poll() is None:
                    agent.proc.kill()
                agent.proc.communicate(timeout=10)
            cancel.reset()


TestTrackedProcess = TrackedProcessMachine.TestCase
TestTrackedProcess.settings = settings(
    max_examples=80, stateful_step_count=10, deadline=None
)


# --- a run that stops ------------------------------------------------------


class _Ending(NamedTuple):
    """How one attempt ends, as the harness reports it, and what the run does."""

    stops: bool
    kept: Outcome | None
    result: dict[str, Any]
    fatal: bool = False


_ENDINGS = {
    "passes": _Ending(False, Outcome.PASS, {}),
    "fails": _Ending(False, Outcome.INFRA_ERROR, {"exit_code": 1}),
    "times_out": _Ending(False, Outcome.TIMEOUT, {"timed_out": True}),
    # The stop killed it: only the spawn knows, and says so.
    "killed": _Ending(True, None, {"exit_code": -9, "cancelled": True}),
    # Dying of its own causes while it stops the run.
    "fails_while_stopping": _Ending(True, Outcome.INFRA_ERROR, {"exit_code": 1}),
    "misconfigured": _Ending(True, None, {}, fatal=True),
    "capped": _Ending(
        True,
        None,
        {"refusal": CliRefusal(RefusalKind.SPENDING_CAP, "cap reached")},
        fatal=True,
    ),
}


def _scripted(endings: list[str]):
    def answer(ctx: RunContext) -> AttemptResult:
        name = endings[ctx.attempt - 1]
        if name == "misconfigured":
            raise HarnessConfigurationError("credentials expired mid-run")
        ending = _ENDINGS[name]
        if ending.stops and not ending.fatal:
            cancel.request()
        fields: dict[str, Any] = {
            "transcript": [ConversationTurn(role="assistant", content="done")],
            "final_output": "done",
            "exit_code": 0,
            "duration_seconds": 0.01,
        }
        return AttemptResult(**{**fields, **ending.result})

    return answer


@settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    endings=st.lists(st.sampled_from(sorted(_ENDINGS)), min_size=1, max_size=6),
    workers=st.integers(1, 3),
)
def test_a_stopped_run_keeps_what_failed_on_its_own_and_drops_what_it_killed(
    tmp_path, endings, workers
):
    spec_path = tmp_path / "sample.eval.yaml"
    spec_path.write_text("tasks: []\n")
    harness = ScriptedHarness(_scripted(endings))
    judge = ScriptedJudge()

    aborted = None
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
            k=len(endings),
            workers=workers,
            timeout=5,
        )
    except RunAborted as exc:
        aborted = exc
        results = exc.results
    finally:
        cancel.reset()

    started = sorted(ctx.attempt for ctx in harness.contexts)
    ran = {i: _ENDINGS[endings[i - 1]] for i in started}
    if workers == 1:
        # One at a time, nothing after the first stop starts.
        stop = next((i for i, e in enumerate(endings, 1) if _ENDINGS[e].stops), None)
        assert started == list(range(1, (stop or len(endings)) + 1))
    # What ran on its own is kept, whatever stopped the run around it.
    kept = [(i, ending.kept) for i, ending in ran.items() if ending.kept is not None]
    attempts = results.task_results[0].attempts
    assert [(a.attempt, a.outcome) for a in attempts] == kept
    assert results.run.interrupted == any(e.stops for e in ran.values())
    assert (aborted is not None) == any(e.fatal for e in ran.values())
    assert judge.calls == sum(1 for _, outcome in kept if outcome is Outcome.PASS)


# --- the hillclimb held-out boundary ---------------------------------------

_spec = importlib.util.spec_from_file_location(
    "climb", ROOT / "skills/hillclimb-skill/climb.py"
)
climb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(climb)

_CLIMB_EDGES: tuple[Edge, ...] = (
    ("[*]", "Unsplit"),
    ("Unsplit", "Unsplit"),
    ("Unsplit", "Split"),
    ("Split", "Split"),
)
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
def _climb_run(draw, names: list[str], measurable: bool = False) -> RunResults:
    """One run over ``names``; ``measurable`` gives every task a measured attempt."""
    tasks = []
    for name in names:
        k = draw(st.integers(1, 3))
        outcomes = draw(
            st.lists(st.sampled_from(_CLIMB_OUTCOMES), min_size=k, max_size=k)
        )
        expected = draw(st.sampled_from(([SKILL], [], None)))
        if measurable:
            outcomes[0] = draw(st.sampled_from((Outcome.PASS, Outcome.TASK_FAIL)))
            expected = [] if expected is None else expected
        secret = _marker(name)
        tasks.append(
            TaskResult(
                task_id=name,
                task_name=name,
                prompt=f"prompt {secret}",
                expect=f"expect {secret}",
                activation_expected=expected,
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


def _measured(results: RunResults, surface: str) -> set[str]:
    """The tasks a split on ``surface`` divides: the ones it measures."""
    if surface == "description":
        return {
            t.task_name
            for t in results.task_results
            if t.activation_expected is not None
        }
    return {
        t.task_name
        for t in results.task_results
        if any(a.outcome is not Outcome.NOT_CHECKED for a in t.attempts)
    }


def _climb(*argv: str) -> str:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        climb.main(list(argv))
    return out.getvalue()


@dataclass
class _Climb:
    """Where a climb stands, and the split it fixed."""

    phase: str = "[*]"
    train: set[str] = field(default_factory=set)
    held_out: set[str] = field(default_factory=set)

    def move(self, to: str) -> None:
        assert (self.phase, to) in _CLIMB_EDGES
        self.phase = to


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
        self.climb = _Climb()
        self.names: list[str] = []
        self.results: RunResults | None = None
        self.saved_state = ""

    def teardown(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)

    def _save(self, results: RunResults) -> RunResults:
        self.run_path.write_text(results.model_dump_json(), encoding="utf-8")
        self.results = results
        return results

    def _split(self, surface: str, seed: int) -> None:
        argv = ["split", str(self.run_path), str(self.state_path)]
        argv += ["--surface", surface, "--seed", str(seed)]
        if surface == "description":
            argv += ["--skill", SKILL]
        _climb(*argv)

    @initialize(
        data=st.data(),
        count=st.integers(climb.MIN_TASKS, 14),
        surface=st.sampled_from(("body", "description")),
        seed=st.integers(0, 10**6),
    )
    def split(self, data, count: int, surface: str, seed: int) -> None:
        self.names = [f"task-{i:02d}" for i in range(count)]
        self.climb.move("Unsplit")
        measured = _measured(self._save(data.draw(_climb_run(self.names))), surface)
        if len(measured) < climb.MIN_TASKS:
            with pytest.raises(SystemExit, match="a climb needs"):
                self._split(surface, seed)
            assert not self.state_path.exists()
            self.climb.move("Unsplit")
            run = data.draw(_climb_run(self.names, measurable=True))
            measured = _measured(self._save(run), surface)
        self._split(surface, seed)
        self.climb.move("Split")
        self.saved_state = self.state_path.read_text(encoding="utf-8")
        state = json.loads(self.saved_state)
        self.climb.train = set(state["train"])
        self.climb.held_out = set(state["held_out"])
        assert self.climb.train | self.climb.held_out == measured

    @rule(data=st.data())
    def reruns(self, data) -> None:
        self._save(data.draw(_climb_run(self.names)))
        self.climb.move("Split")

    @rule(data=st.data(), one=st.booleans())
    def reads_failures(self, data, one: bool) -> None:
        argv = ["failures", str(self.run_path), str(self.state_path)]
        if not one:
            self._assert_hidden(_climb(*argv))
            return
        task = data.draw(st.sampled_from(self.names))
        if task not in self.climb.train:
            with pytest.raises(SystemExit, match="not a train task"):
                _climb(*argv, "--task", task)
            return
        out = _climb(*argv, "--task", task)
        self._assert_hidden(out)
        assert not any(f"## {name} " in out for name in self.climb.train - {task})

    @rule(data=st.data())
    def reads_scores(self, data) -> None:
        assert self.results is not None
        after = data.draw(_climb_run(self.names))
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
            self._split("body", 0)
        self.climb.move("Split")

    def _assert_hidden(self, out: str) -> None:
        for name in self.climb.held_out:
            assert name not in out
            assert _marker(name) not in out

    @invariant()
    def the_split_never_moves(self) -> None:
        assert self.state_path.read_text(encoding="utf-8") == self.saved_state
        assert not self.climb.train & self.climb.held_out


TestHeldOutBoundary = HeldOutBoundaryMachine.TestCase
TestHeldOutBoundary.settings = settings(
    max_examples=30, stateful_step_count=8, deadline=None
)


# --- the charts ------------------------------------------------------------

_EDGE = re.compile(r"^\s*(\[\*\]|\w+)\s*-->\s*(\[\*\]|\w+)", re.MULTILINE)


def _charts() -> list[set[Edge]]:
    """Each ```mermaid block's edges, in the order docs/statecharts.md has them."""
    blocks = re.findall(r"```mermaid\n(.*?)```", STATECHARTS.read_text(), re.DOTALL)
    return [set(_EDGE.findall(block)) for block in blocks]


def test_the_verdict_chart_ends_in_every_outcome():
    reached = {target for _, (_, target) in _VERDICT_PRECEDENCE}
    reached |= set(_JUDGE_VERDICTS.values())

    assert reached == {_state_name(o) for o in Outcome} | {"Discarded"}


def test_each_chart_draws_the_edges_its_machine_moves_along():
    verdict, process, hillclimb = _charts()

    assert verdict == _verdict_edges()
    assert process == set(_PROCESS_EDGES)
    assert hillclimb == set(_CLIMB_EDGES)

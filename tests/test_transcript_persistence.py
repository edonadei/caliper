from __future__ import annotations

from datetime import datetime, timezone

from caliper.schema.results import (
    AggregateScore,
    AttemptRecord,
    Outcome,
    RunMeta,
    RunResults,
    SkillSnapshot,
    TaskResult,
    TranscriptTurn,
)


def test_old_results_json_without_transcript_still_loads() -> None:
    old = {
        "attempt": 1,
        "output": "hi",
        "duration_seconds": 2.0,
        "outcome": "pass",
    }
    record = AttemptRecord.model_validate(old)
    assert record.transcript is None
    assert record.passed is True


def test_attempt_record_transcript_round_trips_through_json() -> None:
    record = AttemptRecord(
        attempt=1,
        output="done",
        duration_seconds=3.5,
        outcome=Outcome.PASS,
        transcript=[
            TranscriptTurn(
                role="assistant",
                content="I'll read the wiki structure.",
            ),
            TranscriptTurn(
                role="tool_use",
                content="[tool: mcp__deepwiki__read_wiki_structure]",
                tool_name="mcp__deepwiki__read_wiki_structure",
                tool_input={"repo": "edonadei/caliper"},
            ),
            TranscriptTurn(
                role="tool_result",
                content='{"sections": ["intro"]}',
                tool_name="mcp__deepwiki__read_wiki_structure",
                tool_output='{"sections": ["intro"]}',
            ),
        ],
    )

    restored = AttemptRecord.model_validate_json(record.model_dump_json())
    assert restored.transcript is not None
    assert len(restored.transcript) == 3
    assert restored.transcript[1].tool_name == "mcp__deepwiki__read_wiki_structure"
    assert restored.transcript[1].tool_input == {"repo": "edonadei/caliper"}
    assert restored.transcript[2].tool_output == '{"sections": ["intro"]}'


def test_run_results_transcript_round_trips_through_json() -> None:
    results = RunResults(
        run=RunMeta(
            spec="demo",
            timestamp=datetime(2026, 7, 12, tzinfo=timezone.utc),
            k=1,
            backend="claude-code",
        ),
        skill_snapshot=SkillSnapshot(path="/fake/SKILL.md"),
        task_results=[
            TaskResult(
                task_id="t1",
                task_name="Task 1",
                attempts=[
                    AttemptRecord(
                        attempt=1,
                        output="ok",
                        duration_seconds=1.0,
                        outcome=Outcome.INFRA_ERROR,
                        transcript=[
                            TranscriptTurn(
                                role="assistant",
                                content="starting",
                            )
                        ],
                    )
                ],
                successes=0,
                unusable=1,
                pass_at_k=None,
            )
        ],
        aggregate=AggregateScore(avg_score=0.0, per_task=[]),
    )

    restored = RunResults.model_validate_json(results.model_dump_json())
    attempt = restored.task_results[0].attempts[0]
    assert attempt.outcome == Outcome.INFRA_ERROR
    assert attempt.transcript is not None
    assert attempt.transcript[0].content == "starting"

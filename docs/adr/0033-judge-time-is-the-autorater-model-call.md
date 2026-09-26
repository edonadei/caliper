# Judge time is the autorater's model call

`judge_seconds` was measured by `assemble_attempt` around the whole
`judge.evaluate` call, and kept only when the task had an `expect:`. A task with
both `expect:` and `assert:` therefore counted its assert script as judge time,
and so did the first attempt's harness construction.

The judge now times the autorater's `run_prompt` call itself and reports it on
`JudgeResult.autorater_seconds`, which becomes `judge_seconds`. Judge time is
the model call and nothing else (docs/CONTEXT.md → Judge time).

This redefines a saved field. For a task with both checks, a run saved before
this change recorded more judge time than the same run records now, so judge
time from before and after it is not comparable for those tasks. The field
was kept rather than renamed. Its meaning never included the script, and
renaming it would break every saved run to fix a measurement error.

# Orchestration test audit

ISSUES. Audited SHA `e8a9302fa355653f95ff7c6d46b4d94ece9cae3c`, which was also HEAD when inspected.

## Method and evidence

Read the audit contract, Test Behavior, Not Implementation skill, How skill, Unslop skill, and `.github/CONTRIBUTING.md`. This is the How explorer's orchestration slice for the parent to synthesize. Read every assigned file in full, including test helpers, parametrization, assertions, assertion source strings, and exception contexts. Read `tests/conftest.py` in full. Followed each assertion to the production owner and compared the actual reached inputs, exits, and mock boundaries. Inspected Git history for the constructor type test, retry refusal tests, fail-fast trigger-probe regression, and retired baseline flag.

This agent executed no tests and made no test or production edits. Case counts and selected durations below come from the parent's already executed `docs/qa/test-suite-audit-2026-10-10/baseline.xml`, not an independent verification run. All 324 cases in this assigned slice passed in that artifact; none was skipped. The parent's full run had unrelated failures outside this slice. This agent ran no mutation experiment. The parent separately supplied `mutant-cap.txt`, which this agent inspected: bypassing spending-cap handling caused surviving `test_a_spending_cap_raises_instead_of_retrying` to fail because SpendingCapReached was not raised. That is one executed parent mutation proof for D2, not general mutation evidence for the slice. Other classifications are source-inspected judgments.

Read these production implementations in full: `caliper/attempt.py`, `caliper/runner.py`, `caliper/retry.py`, `caliper/cancel.py`, `caliper/commands/run.py`, `caliper/commands/engine.py`, `caliper/environment.py`, `caliper/judge/base.py`, `caliper/judge/eval_judge.py`, `caliper/workdir.py`, `caliper/activation.py`, `caliper/sandbox.py`, `caliper/commands/compare.py`, and `caliper/commands/list_cmd.py`.

Read relevant sections of `caliper/harness/base.py`: RunContext ownership, prompt staging, run/run_prompt lifecycle, and _execute. Read backend prompt command/output/error extraction implementations in `caliper/harness/claude_code.py`, `codex.py`, `hermes.py`, and `pi.py`; `caliper/schema/spec.py` target parsing and task ID assignment; `caliper/schema/results.py` outcome predicates, AttemptRecord, task counts/metrics, usage, ablation metadata, measured headline and noise counts; `caliper/compare.py` ablation recognition and diff warnings; `caliper/reporter.py` success-cell branching. Cross-slice overlap searches inspected selected tests in `test_environment.py`, `test_compare_cli.py`, `test_cli_harness.py`, `test_usage.py`, `test_reporter.py`, and `test_scoring.py`. Those other files were not audited in full by this agent.

## Counts and per-module disposition

266 AST test functions, 324 collected pytest cases, and 654 Python `assert` statements within those functions. AST assertions do not count `pytest.raises` contracts, barrier failures, or assertion scripts executed by subprocesses. Loop iterations within one test remain one pytest case. In particular the constructor type test loops over four backends but is one case.

| File, all under `tests/` | AST functions | Pytest cases | Disposition |
| --- | ---: | ---: | --- |
| test_attempt.py | 41 | 45 | Keep verdict/precedence, admissibility, metadata, and namespaced activation tests. Two identical-input pairs can consolidate after preserving assertions. |
| test_attempt_parallelism.py | 6 | 7 | Keep barriers, per-task concurrency, round-robin submission. Consolidate the repeated four-party barrier run and force an actual out-of-order completion for its order claim. |
| test_cancel.py | 18 | 22 | Keep all. Real process trees, orphaned tools, pipe holders, identity tracking, interrupted record policy, and salvage are material contracts. |
| test_judge.py | 44 | 63 | Delete one constructor type tautology. Keep malformed-response cases, Rule B, script execution, backend prompt transports and cancellation. Strengthen timing assertion. |
| test_runner.py | 26 | 29 | Keep all. Lifecycle hooks, real script checks, metadata aggregation, callbacks, IDs and fail-fast integration are distinct from pure assembly tests. |
| test_run_seam.py | 5 | 5 | Keep model selection, caller immutability and list ownership. Strengthen invocation isolation test before claiming it proves fresh backend state. |
| test_retry.py | 20 | 20 | Delete two exact reached-input duplicates. Keep loop bounds, no-retry contracts, merge arithmetic and integration. Strengthen capped-run salvage fixture. |
| test_measure_and_mark.py | 12 | 12 | Keep latency transfer/roll-up and partial-run reader warnings. Strengthen regression CLI test's premise/output assertion. |
| test_ablation.py | 38 | 38 | Keep all behavior dimensions; four groups of identical runs can consolidate, removing five functions only after moving distinct assertions. Legacy membership guards remain live contracts. |
| test_run_cli.py | 31 | 41 | Keep CLI forwarding, exit codes, progress, discovery, saving and login controls. Retired flag is a deliberate removal contract, not an unconditional cut. |
| test_engine.py | 8 | 14 | Keep resolution and diagnosis branches. `None` results are real acceptance checks with rejection controls. |
| test_parse_target.py | 1 | 12 | Keep all 12 literal parser outputs. CLI resolution does not replace boundary cases such as empty halves/multiple colons. |
| test_not_checked.py | 16 | 16 | Keep score/render/noise distinctions. Consolidate predicate with identical-input render test while retaining literal bool assertion. Replace supposed streak regression with real runner behavior. |

Unconditional safe-delete recommendation is 3 functions / 3 cases. Conditional consolidation is 8 groups / 9 additional functions / 9 cases removed, leaving every existing assertion. These figures do not overlap. The optional consolidated suite would have 254 functions / 312 cases in this slice. They are recommendations only; no cuts are made.

## Unconditional safe-delete candidates

### D1. Constructor type tautology

`tests/test_judge.py:82`, `test_eval_judge_always_returns_eval_judge_instance`.

Current assertion is only `assert isinstance(judge, EvalJudge)` after directly constructing `EvalJudge(backend=backend)` for codex, claude-code, pi, and hermes. The current constructor merely assigns backend/model/harness and does not dispatch to backend-dependent classes. It never calls evaluate or run_prompt. It adds no verdict, routing, payload, error, or lifecycle check. Git history shows it arrived with `f30d5e8` when four judge CLI drivers were collapsed into the backend seam. It now directly constructs the class instead of checking a meaningful factory's result.

Surviving exact tests use the same `EvalJudge` constructor path and then exercise actual backend dispatch:

- `tests/test_judge.py:300`, `test_eval_judge_claude_code_invokes_claude_cli`, evaluates and checks literal pass, reasoning, argv/model/prompt and strict connector isolation. The alias `claude` reaches claude-code normalization.
- `tests/test_judge.py:347`, `test_claude_judge_extracts_concrete_model_from_envelope`, constructs with exact `backend="claude-code"` and verifies a pass and concrete model.
- `tests/test_judge.py:381`, `test_codex_judge_uses_output_last_message`, constructs codex and checks verdict, output-file use, argv/model, stdin, connectors and cwd.
- `tests/test_judge.py:583`, `test_hermes_backend_is_a_valid_judge`, constructs hermes and checks pass and the real transport argv/model.
- `tests/test_judge.py:660`, `test_pi_backend_is_a_valid_judge`, constructs pi and checks stream parsing, pass and argv/model.

These survivors differ in input task/model because the deleted test has no evaluate inputs or failure behavior to preserve. This is a no-behavior test, not a claim that a stronger integration test is generally equivalent to every unit test. The runtime saved is tiny: the parent's baseline reports 0.001s for this case.

### D2. Zero-exit spending cap duplicates the earlier cap test

`tests/test_retry.py:389`, `test_a_cap_on_a_zero_exit_still_stops_the_run`.

Current assertion is `pytest.raises(SpendingCapReached)` around `invoke_with_retry(_queue(_result(**CAPPED))[0], NO_WAIT)`. `CAPPED` sets SPENDING_CAP refusal and salvaged output, with `_result` default `exit_code=0`.

Exact surviving test `tests/test_retry.py:147`, `test_a_spending_cap_raises_instead_of_retrying`, sends the identical `_result(**CAPPED)` and policy. It asserts the same exception, exactly one invocation, and the usage-limit wording. Its queue additionally holds a clean result, but production raises immediately on the first cap at `caliper/retry.py:155`; that trailing fixture is never consumed. Therefore both exercise the exact zero-exit first input and exception path. Git `296ecb6` changed the former raw-output regression to this shared CAPPED refusal fixture, making the specialized zero-exit case redundant with the earlier test. This recommendation keeps the zero-exit regression behavior.

### D3. Identical answer-about-limits no-respawn test

`tests/test_retry.py:380`, `test_a_passing_attempt_that_mentions_a_throttle_is_not_respawned`.

Current assertion is only `len(calls) == 1` after sending `_result(output=ANSWER_ABOUT_LIMITS)` to `invoke_with_retry(..., NO_WAIT)`.

Exact surviving test `tests/test_retry.py:370`, `test_a_passing_attempt_that_mentions_a_cap_does_not_abort_the_run`, sends the identical first input and policy and asserts one invocation, zero retries, and exact preserved final output. Despite the names, both use the same prose fixture containing cap, throttle, 429, quota, usage-limit and 503 language with no refusal. The deleted test queues a second clean result; production sees no refusal on the first result and breaks at `caliper/retry.py:161`, before that trailing fixture matters. A bad implementation that retries would consume a second item in the deletion candidate and fail its count, and would exhaust the survivor's one-item queue and fail it too. Git `296ecb6` explicitly moved retry decisions from regexes to the typed CLI refusal; preserve the stronger prose negative control rather than both names.

## Conditional consolidation, not deletion of distinct assertions

Each group below uses the same production entry point, reached inputs and collaborators. Existing tests have different output assertions, so none of the removed functions is unconditionally redundant. Move all specified assertions into the named survivor first. No parametrized cases are involved.

### C1. Timeout evidence and admissibility

Remove `tests/test_attempt.py:406`, `test_a_kept_observation_is_evidence_and_never_a_verdict`, only after extending survivor `tests/test_attempt.py:389`, `test_a_truncated_transcript_keeps_an_activation_it_did_see`.

Both assemble the identical timed-out result with exit 124, error `timeout`, Read `/skills/tdd/SKILL.md`, `_detector()`, expected `['tdd']` and default task/judge/sandbox. Survivor asserts TIMEOUT and activated `['tdd']`; candidate asserts `activation_passed is None`, `activation_scored is False`, and `activation_observed is False`. All five assertions must remain. This preserves the distinction between retained raw evidence and admissible observations.

### C2. Setup failure record and absence of run metadata

Remove `tests/test_attempt.py:534`, `test_a_failed_setup_hook_reports_no_run_level_facts`, only after extending `tests/test_attempt.py:521`, `test_a_failed_setup_hook_is_infra_error_with_its_reason_as_evidence`.

Both input `SetupFailed('setup exited 7')` with the same defaults; the latter explicitly constructs the same default RecordingJudge. Survivor asserts outcome, reason, empty output, absent activation/scoring and judge skipped. Candidate adds absent resolved_model, loaded_user_customizations and judge_model. Move those three metadata assertions. Without moving them, the candidate checks a separate observable contract and stays.

### C3. One four-party barrier run

Remove `tests/test_attempt_parallelism.py:115`, `test_attempts_are_recorded_in_order_whatever_order_they_finish`, only after moving `[a.attempt ...] == [1, 2, 3, 4]` into `tests/test_attempt_parallelism.py:94`, `test_attempts_of_a_single_task_run_in_parallel`.

Both run one task with BarrierHarness(4), ScriptedJudge, k=4, workers=4, timeout=5 and the same spec fixture. The survivor proves all four outcomes PASS, which can only happen after all peers rendezvous. Candidate checks sorted attempt numbers. Preserve both. The current barrier releases together and does not force an out-of-order completion; strengthen the combined harness with deterministic Events before claiming ordering independent of completion sequence. This consolidation saves an identical pool/workdir setup, not the four workers' behavior.

### C4. Reduced skill environment, marker and snapshots

Remove `tests/test_ablation.py:172`, `test_the_ablated_names_are_recorded_on_run_meta`, and `:182`, `test_snapshots_cover_only_the_installed_skills`, after extending survivor `:147`, `test_ablate_installs_the_neighbourhood_minus_the_named_skill`.

All use `_spec_with_two_skills(tmp_path)` with no activates, `_recording()`, `_run_spec(..., ablate=['subject'])`. Candidate marker assertions are `ablated == ['subject']`, `ablated_skills == ['subject']`, `ablated_servers == []`; snapshot assertion is names `['keeper']`. Survivor asserts the harness got exactly `[['keeper']]`. Keep results from the survivor run and assert every field. Three independent contract assertions then share one identical invocation. Net two functions/cases removed.

### C5. Removed server handover and metadata

Remove `tests/test_ablation.py:231`, `test_the_ablated_server_is_recorded_qualified_on_run_meta`, after extending survivor `:209`, `test_ablate_removes_a_declared_mcp_server`.

Both use `_spec_with_skill_and_server(tmp_path)` without activates/collision, `_recording()`, ablate `['weather']`. Survivor asserts server handover `[{}]` and skill handover `[['subject']]`; candidate asserts qualified marker `['mcp:weather']`, no ablated skills, server `['weather']`, and recorded remaining servers `[]`. Preserve the literal empty mapping versus None distinction and all metadata assertions.

### C6. Full server handover and metadata

Remove `tests/test_ablation.py:242`, `test_the_servers_a_run_kept_are_recorded_on_run_meta`, after extending survivor `:222`, `test_a_run_without_ablate_hands_over_every_server`.

Both use the same no-collision skill/server fixture, same recording harness and no ablation. Survivor checks the exact McpServer mapping. Candidate checks empty ablation marker and recorded server `['weather']`. Preserve those checks on the survivor's results. No path-resolution behavior is lost because args are identically `['w.py']`.

### C7. Dropped activation expectation with retained observation

Remove `tests/test_ablation.py:307`, `test_an_ablated_run_drops_the_activation_expectation`, after extending survivor `:326`, `test_the_observation_survives_even_though_the_verdict_does_not`.

Both use `_spec_with_two_skills(..., activates=['subject', 'keeper'])`, `_recording()`, ablate `['subject']`. Candidate asserts task activation_expected and activation_score are None; survivor asserts attempt activated `['keeper']` and activation_passed None. Preserve all four. Do not remove `:318`, `test_an_activates_naming_the_ablated_skill_is_not_a_validation_error`, on this evidence: its authored expectation is `['subject']`, a different exact input.

### C8. Trigger-only predicate and rendered cell

Remove `tests/test_not_checked.py:136`, `test_trigger_only_task_is_detected`, after extending survivor `:140`, `test_trigger_only_task_reads_as_a_skip_not_an_error`.

Both consume the identical `_trigger_task()` literal two-NOT_CHECKED result. Survivor calls `_success_cell`, which directly checks `tr.trigger_only` before returning the literal split output `['—', 'probe']` and dim styles. Candidate separately asserts `trigger_only is True`. Retain that literal bool assertion on the same TaskResult before rendering so a truthy non-bool regression is also still caught. Do not use `test_trigger_only_survives_one_timeout_among_k` as the equivalent survivor because its mixed outcomes differ.

## Strengthen findings and explicit behavior gaps

1. `tests/test_not_checked.py:239`, `test_a_healthy_trigger_probe_resets_the_fail_fast_streak`, never calls run. It only pins NOT_CHECKED.is_execution_noise False and JUDGE_ERROR.is_execution_noise True. The NOT_CHECKED half already appears at `:34`, `test_not_checked_is_neither_usable_nor_noise`; the JUDGE_ERROR half has real derived-count protection at `:48`, `test_judge_errors_are_still_counted_as_noise`, and real fail-fast integration at `tests/test_runner.py:352`, `test_runner_fail_fast_does_not_reset_streak_on_judge_error`. Those are not exact substitutes for the claimed NOT_CHECKED reset path, so recommend replacement, not deletion. Git `c68b226` records a real defect where a healthy trigger probe between timeouts failed to reset the streak. Construct an activates-only task with scripted TIMEOUT, clean NOT_CHECKED, TIMEOUT, then another healthy result; run fail_fast_unusable=2, k=4, workers=1, assert all four records/outcomes. The current test would pass if `_run_task_chain` made NOT_CHECKED neutral again.

2. `tests/test_retry.py:280`, `test_a_spending_cap_aborts_the_run_and_saves_what_ran`, supplies a cap on every first invocation and asserts an empty attempt list. It proves abort/interrupted/no fabricated record, which should stay, but does not test preservation of preceding completed attempts as its name suggests. `tests/test_cancel.py:258`, `test_a_fatal_error_mid_run_salvages_the_attempts_that_ran`, keeps one attempt before a HarnessConfigurationError, but that is another exception type. Add clean first attempt then typed SPENDING_CAP and assert RunAborted cause, exact saved first attempt, interrupted and no third invocation. This protects the spending-cap branch as a partial-run contract.

3. `tests/test_judge.py:813`, `test_the_autorater_times_its_own_call`, only checks autorater_seconds not None and >=0. It would accept a hardcoded 0 without timing any prompt. `tests/test_judge.py:824`, `test_an_assert_script_beside_the_autorater_is_not_judge_time`, also accepts hardcoded 0; its real 0.3s script proves excluding script time but no positive model-duration contract. Patch the judge module's monotonic clock with a deterministic start/end pair and assert literal elapsed duration, then retain separate exclusion-of-script timing coverage. Do not delete existing timing-transfer tests in test_measure_and_mark: those prove recording the judge's value into AttemptRecord and UsageTotals, a different owner.

4. `tests/test_measure_and_mark.py:238`, `test_compare_reports_a_regression_without_failing`, only asserts CLI exit 0. Its saved fixtures really contain scores 1.0 and 0.25, but a compare command that returns early without diffing or printing anything would also pass. Assert the regression output or invoke JSON and assert has_regression and literal delta -0.75 plus exit 0. Keep the exit assertion because non-gating compare is deliberate at `caliper/commands/compare.py:84`. The cross-slice test_compare_cli distinct-run acceptance test uses different inputs and does not replace the regression branch.

5. `tests/test_run_seam.py:95`, `test_each_invocation_gets_its_own_context`, calls `_run` twice, and `_run` constructs a new RunContext each time. Recording distinct objects/attempts largely records that helper's own construction. It does not prove that CliHarness copies one caller-owned context or clears mutable state between retries. The model-default test at `:55`, `test_the_backends_own_model_answers_a_request_that_names_none`, does prove caller model stays None when resolved, and `tests/test_retry.py:415`, `test_each_invocation_of_an_attempt_gets_its_own_context`, directly proves runner fresh contexts across the same retried attempt. Neither is an exact deletion substitute for all caller/backend interactions. Strengthen the seam test by running the same caller context twice and asserting hooks received distinct contexts; mutate the first received lists and prove caller/second invocation contents remain literal originals. Keep the existing list ownership test because it directly mutates RunContext and observes caller state.

6. `tests/test_attempt_parallelism.py:115` does not guarantee its name's out-of-order completion premise. Use explicit events to finish a known order, collect attempt callback order to verify the premise, and assert result records sorted by attempt number. Keep parallelism barriers regardless of this weakness. `tests/test_attempt_parallelism.py:151`, `test_fail_fast_keeps_a_task_sequential`, uses a 0.05s overlap window and peak count rather than a barrier; the source mechanism is correct and the result is real, but deterministic coordination would make its concurrency premise stronger.

These are source-inspected gaps in this slice. Other full-suite slices may add coverage; no assertion of global absence is made.

## Why most apparent weak shapes stay

Predetermined ScriptedHarness / ScriptedJudge outcomes are inputs to orchestration, not implementations of the behavior under test. `run` still invokes hooks, builds environments/contexts, schedules attempts, handles retries, classifies results, excludes killed records, aggregates metrics/metadata, and emits callbacks. Tests that assert literal Outcome, counters, evidence, concrete model or exact transferred transport payload can fail for defects in those owners. Asserting a mock's count can also be a real spend contract: not paying the judge for TIMEOUT/CHEAT/NOT_CHECKED, never retrying a crash, and stopping queued attempts after cancellation.

Pure attempt tests isolate verdict precedence with a real ActivationDetector and stub sandbox/judge. Runner setup, workdir and cleanup tests run real shell/Python code, so they protect lifecycle and stale-artifact behavior the pure tests cannot. CLI fake-run tests protect Typer parsing, construction of Engine, callback wiring, exit codes and persistence. `test_run_cli_resolves_backend_and_judge_model_targets` is not replaced by `test_parse_target` or `test_engine`: neither of those proves both user flags arrive at the correct constructors. Default timeout forwarding is a public CLI default exercised by the real command parser, not an unused constant pin.

Examples of meaningful absence checks to keep:

- `test_attempt::test_an_attempt_the_cancellation_killed_is_not_evidence` returns None and verifies no judge spend; its positive measured-attempt neighbor proves records exist.
- `test_retry::test_merge_leaves_usage_none_when_no_invocation_reported_any` keeps unknown usage distinct from fabricated zero, paired with a positive sum test.
- `test_engine::test_a_spec_without_expect_never_needs_the_judge_cli` accepts an absent CLI only for assert-only tasks; adjacent tests reject each named/default missing-CLI path and accept an installed CLI.
- Ablation empty server mapping tests preserve `{}` versus None because the authored environment must remain isolated after every declared server is removed.
- Complete-run compare/list negative controls suppress only interrupted-run warnings, while positive tests prove warnings and markers are emitted.
- Trigger-only enum predicates are cheap named domain classifications whose consumers use different execution/activation/noise categories. They are not numerical tuning constants. The mislabeled streak test should be replaced with the real operation.

`test_judge_input_can_be_rebuilt_from_the_saved_transcript` compares production render_judge_prompt with the prompt a real evaluate call passed to ScriptedPrompt. It is partly self-referential for formatting, but the contract is compatibility between two representations, ConversationTurn and persisted TranscriptTurn. Keep that relation. It does not prove the expected prompt prose/role formatting; a renderer that always returned one fixed string could satisfy that relation. `test_prompt_version_moves_when_transcript_formatting_changes` tests hash response to a changed formatter and is a real mechanism check, not a pinned hash. `test_run_records_what_rebuilds_the_judge_input` compares saved version with JUDGE_PROMPT_VERSION because version propagation is the contract. Do not replace either with a frozen hash.

## Retired and legacy guards

`tests/test_run_cli.py:522`, `test_baseline_is_no_longer_an_option`, asserts exit 2 and `No such option` for the exact retired --baseline flag. Git `5984381` says --ablate replaced it in v0.10.0 and the hidden migration shim survived six releases before removal. The current behavior is Typer rejecting an absent option. This is low-value retirement coverage if the team no longer needs to prevent accidental reintroduction of that shim, but it is not an exact duplicate of another test: no surviving test rejects this exact flag. Keep by default; a policy choice to stop protecting removed flags can delete it consciously, outside the 3 safe cuts.

Ablation legacy mcp_servers=None cases remain live read-side compatibility rules, not dead migration scaffolding. Older saved JSON can still be loaded, and compare must treat unknown membership differently from empty membership. Each case covers either honest bare-agent labeling or pairing with an unknown full side; no same-input stronger survivor was found.

## Expensive tests that stay

The parent's baseline gives useful cost scale, not stable benchmark results. The three unconditional cuts total roughly 0.001s plus sub-millisecond retry cases. Main orchestration runtime is in deliberate real-process integration tests, which should not be cut for speed:

- `tests/test_cancel.py:429`, `test_stopping_an_attempt_kills_tools_in_their_own_process_groups`, cancel/timeout cases prove child and grandchild sessions die. Timeout case was 1.042s.
- `test_cancel.py:468`, `test_stopping_an_attempt_reaches_a_tool_after_its_agent_exits`, keeps an orphan and a later spawned leaf observable, then proves neither survives. Timeout case was 2.035s.
- `test_cancel.py:530`, `test_stopping_does_not_hang_on_an_unidentifiable_pipe_holder`, deliberately disables descendant/tag discovery and checks bounded completion plus no leaked communication worker. Timeout case was 2.014s; cancel case 1.110s.
- `test_cancel.py:626`, `test_timeout_keeps_partial_output_when_a_pipe_holder_survives`, verifies literal retained output even after bounded drain gives up. Was 2.009s. It is not redundant with the no-hang test, whose output is empty.
- `test_cancel.py:342`, `test_cancelling_a_finished_agent_with_exited_tools_keeps_its_attempt`, verifies historical descendants cannot mark a finished CLI as killed. It guards an observation-preservation race using a real watched child.
- `test_cancel.py:564`, `test_large_prompt_reaches_agent_after_a_slow_stdin_start`, sends a million-byte prompt to a slow reader and verifies its exact byte count; mocks cannot catch blocked stdin/communicate behavior. Was 0.336s.
- `tests/test_runner.py:830`, `test_a_hanging_setup_is_an_infra_error_not_a_stuck_worker`, proves real bounded hook execution and evidence, about 1.076s.
- `tests/test_judge.py:796`, `test_an_assertion_that_hangs_has_no_verdict`, proves a real assertion timeout yields no verdict, about 1.090s.
- `test_attempt_parallelism.py:94` and `:132` barrier tests prove actual worker overlap within/across tasks. Replacing them with submit-count mocks would lose the scheduling contract.

Paths/lines in this report refer to the pinned source. The assertion inventory below gives exact function locations for every assigned test and captures every Python assert/pytest.raises expression. Test bodies and helper/script behavior were also read; the inventory is an index, not a heuristic verdict based on assertion syntax.

## Complete assertion inventory

### test_attempt.py

- `test_a_clean_attempt_with_a_passing_verdict_is_a_pass` at `tests/test_attempt.py:92`. KEEP. line 105: `assert assembled.record.outcome is Outcome.PASS`; line 106: `assert assembled.record.autorater_passed is True`; line 107: `assert assembled.record.assert_passed is True`; line 108: `assert judge.calls == 1`.
- `test_a_failing_verdict_is_a_task_fail` at `tests/test_attempt.py:111`. KEEP. line 116: `assert assembled.record.outcome is Outcome.TASK_FAIL`.
- `test_the_attempt_record_carries_the_harness_result_verbatim` at `tests/test_attempt.py:119`. KEEP. line 133: `assert record.duration_seconds == 2.25`; line 134: `assert record.output == 'the answer'`; line 135: `assert record.usage == usage`; line 136: `assert [t.content for t in record.transcript or []] == ['hi']`.
- `test_the_judges_resolved_model_is_reported_back` at `tests/test_attempt.py:139`. KEEP. line 144: `assert assembled.judge_model == 'claude-sonnet-5'`.
- `test_no_judge_model_when_the_autorater_reports_none` at `tests/test_attempt.py:147`. KEEP. line 148: `assert _assemble(_result()).judge_model is None`.
- `test_a_timeout_never_reaches_the_judge` at `tests/test_attempt.py:154`. KEEP. line 161: `assert assembled.record.outcome is Outcome.TIMEOUT`; line 162: `assert assembled.record.assert_evidence == 'timeout'`; line 163: `assert judge.calls == 0`.
- `test_a_nonzero_exit_is_infra_error_with_the_exit_code_as_evidence` at `tests/test_attempt.py:166`. KEEP. line 171: `assert assembled.record.outcome is Outcome.INFRA_ERROR`; line 172: `assert assembled.record.assert_evidence == 'harness exited 1'`; line 173: `assert judge.calls == 0`.
- `test_no_observed_model_call_is_infra_error_and_skips_the_judge` at `tests/test_attempt.py:176`. KEEP. line 184: `assert assembled.record.outcome is Outcome.INFRA_ERROR`; line 185: `assert assembled.record.assert_evidence == "no model call observed: nothing parsed from the agent's stream and no tokens reported"`; line 189: `assert judge.calls == 0`.
- `test_a_forbidden_file_read_is_a_cheat_and_skips_the_judge` at `tests/test_attempt.py:192`. KEEP. line 199: `assert assembled.record.outcome is Outcome.CHEAT`; line 200: `assert assembled.record.cheat_evidence == ['/x/answers.txt']`; line 201: `assert judge.calls == 0`.
- `test_a_task_with_no_execution_check_is_not_checked_and_skips_the_judge` at `tests/test_attempt.py:204`. KEEP. line 211: `assert assembled.record.outcome is Outcome.NOT_CHECKED`; line 212: `assert judge.calls == 0`.
- `test_a_cheat_outranks_a_missing_execution_check` at `tests/test_attempt.py:215`. KEEP. line 223: `assert assembled.record.outcome is Outcome.CHEAT`.
- `test_a_timeout_outranks_a_missing_execution_check` at `tests/test_attempt.py:226`. KEEP. line 234: `assert assembled.record.outcome is Outcome.TIMEOUT`; line 235: `assert judge.calls == 0`.
- `test_an_errored_judge_is_a_judge_error_not_a_task_fail` at `tests/test_attempt.py:238`. KEEP. line 242: `assert assembled.record.outcome is Outcome.JUDGE_ERROR`.
- `test_activation_is_observed_and_scored_on_a_judged_attempt` at `tests/test_attempt.py:252`. KEEP. line 259: `assert assembled.record.activated == ['tdd']`; line 260: `assert assembled.record.activation_passed is True`.
- `test_an_unexpected_activation_fails_the_exact_set_match` at `tests/test_attempt.py:263`. KEEP. line 270: `assert assembled.record.activated == ['grilling']`; line 271: `assert assembled.record.activation_passed is False`.
- `test_activation_is_observed_but_not_scored_when_nothing_was_expected` at `tests/test_attempt.py:274`. KEEP. line 282: `assert assembled.record.activated == ['tdd']`; line 283: `assert assembled.record.activation_passed is None`.
- `test_a_built_in_skill_is_shown_but_does_not_fail_silence` at `tests/test_attempt.py:295`. KEEP. line 306: `assert assembled.record.activated == []`; line 307: `assert assembled.record.activation_passed is True`; line 308: `assert assembled.record.builtin_activated == ['claude-api']`.
- `test_a_plugin_skill_read_is_not_credited_to_a_built_in_namesake` at `tests/test_attempt.py:311`. KEEP. line 324: `assert assembled.record.activated == ['review:debug']`; line 325: `assert assembled.record.builtin_activated == []`.
- `test_built_in_activation_is_unobserved_when_the_backend_cannot_list_them` at `tests/test_attempt.py:328`. KEEP. line 335: `assert assembled.record.builtin_activated is None`.
- `test_activation_rides_on_a_cheat_too` at `tests/test_attempt.py:338`. KEEP. line 346: `assert assembled.record.outcome is Outcome.CHEAT`; line 347: `assert assembled.record.activated == ['tdd']`; line 348: `assert assembled.record.activation_passed is True`.
- `test_activation_rides_on_a_trigger_probe` at `tests/test_attempt.py:351`. KEEP. line 359: `assert assembled.record.outcome is Outcome.NOT_CHECKED`; line 360: `assert assembled.record.activation_passed is True`.
- `test_a_trigger_probe_expecting_nothing_does_not_pass_when_the_agent_never_ran` at `tests/test_attempt.py:363`. KEEP. line 372: `assert assembled.record.outcome is Outcome.INFRA_ERROR`; line 373: `assert assembled.record.activation_passed is None`.
- `test_a_truncated_transcript_yields_no_observation_rather_than_an_empty_set` at `tests/test_attempt.py:376`. KEEP. line 384: `assert assembled.record.outcome is Outcome.TIMEOUT`; line 385: `assert assembled.record.activated is None`; line 386: `assert assembled.record.activation_passed is None`.
- `test_a_truncated_transcript_keeps_an_activation_it_did_see` at `tests/test_attempt.py:389`. KEEP. line 402: `assert assembled.record.outcome is Outcome.TIMEOUT`; line 403: `assert assembled.record.activated == ['tdd']`.
- `test_a_kept_observation_is_evidence_and_never_a_verdict` at `tests/test_attempt.py:406`. CONSOLIDATE C1. line 419: `assert assembled.record.activation_passed is None`; line 420: `assert assembled.record.activation_scored is False`; line 421: `assert assembled.record.activation_observed is False`.
- `test_user_skill_activation_counts_as_an_unexpected_activation` at `tests/test_attempt.py:424`. KEEP. line 433: `assert assembled.record.activated == ['personal']`; line 434: `assert assembled.record.activation_passed is False`.
- `test_plugin_skill_file_reads_are_observed_under_the_namespaced_name` at `tests/test_attempt.py:437`. KEEP. line 451: `assert assembled.record.activated == ['review:review']`; line 452: `assert assembled.record.activation_passed is False`.
- `test_plugin_read_does_not_credit_a_declared_skill_with_the_same_basename` at `tests/test_attempt.py:455`. KEEP. line 466: `assert assembled.record.activated == ['plugin:review']`; line 467: `assert assembled.record.activation_passed is False`.
- `test_relative_plugin_reads_are_observed_under_the_namespaced_name` at `tests/test_attempt.py:471`. KEEP. line 487: `assert assembled.record.activated == ['review:review']`.
- `test_windows_plugin_reads_are_observed_under_the_namespaced_name` at `tests/test_attempt.py:490`. KEEP. line 506: `assert assembled.record.activated == ['review:review']`.
- `test_windows_reads_of_a_declared_skill_are_observed` at `tests/test_attempt.py:509`. KEEP. line 515: `assert assembled.record.activated == ['review']`.
- `test_a_failed_setup_hook_is_infra_error_with_its_reason_as_evidence` at `tests/test_attempt.py:521`. KEEP. line 526: `assert assembled.record.outcome is Outcome.INFRA_ERROR`; line 527: `assert assembled.record.assert_evidence == 'setup exited 7'`; line 528: `assert assembled.record.output == ''`; line 529: `assert assembled.record.activated is None`; line 530: `assert assembled.record.activation_passed is None`; line 531: `assert judge.calls == 0`.
- `test_a_failed_setup_hook_reports_no_run_level_facts` at `tests/test_attempt.py:534`. CONSOLIDATE C2. line 537: `assert assembled.resolved_model is None`; line 538: `assert assembled.loaded_user_customizations is None`; line 539: `assert assembled.judge_model is None`.
- `test_an_attempt_the_cancellation_killed_is_not_evidence` at `tests/test_attempt.py:542`. KEEP. line 549: `assert assembled is None`; line 550: `assert judge.calls == 0`.
- `test_a_measured_attempt_reports_the_model_and_customizations_it_saw` at `tests/test_attempt.py:553`. KEEP. line 558: `assert assembled.resolved_model == 'model-x'`; line 559: `assert assembled.loaded_user_customizations == ['a', 'b']`.
- `test_a_refusal_despite_a_zero_exit_is_infra_error_with_its_message` at `tests/test_attempt.py:578`. KEEP. line 588: `assert record.outcome is Outcome.INFRA_ERROR`; line 589: `assert record.assert_evidence == '429 rate limit'`; line 590: `assert not judged`.
- `test_a_refusal_is_the_evidence_even_when_no_model_call_was_seen` at `tests/test_attempt.py:593`. KEEP. line 600: `assert _assemble(result).record.assert_evidence == '429 rate limit; retry at 5pm'`.
- `test_an_answer_that_mentions_a_limit_is_still_judged` at `tests/test_attempt.py:603`. KEEP. line 606: `assert _reaches_the_judge(_result(final_output='I added handling for the 429 rate limit.'))`.
- `test_no_observed_model_call_in_any_form_is_infra_error` at `tests/test_attempt.py:629`. KEEP. line 634: `assert record.outcome is Outcome.INFRA_ERROR`; line 635: `assert not judged`.
- `test_an_unparsed_attempt_that_spent_tokens_is_still_judged` at `tests/test_attempt.py:638`. KEEP. line 641: `assert _reaches_the_judge(_result(salvaged=True, usage=TokenUsage(input_tokens=10, output_tokens=5)))`.
- `test_an_answer_with_no_transcript_or_usage_is_still_judged` at `tests/test_attempt.py:646`. KEEP. line 649: `assert _reaches_the_judge(_result(transcript=[]))`.

### test_attempt_parallelism.py

- `test_attempts_of_a_single_task_run_in_parallel` at `tests/test_attempt_parallelism.py:94`. KEEP. line 112: `assert [a.outcome for a in results.task_results[0].attempts] == [Outcome.PASS] * 4`.
- `test_attempts_are_recorded_in_order_whatever_order_they_finish` at `tests/test_attempt_parallelism.py:115`. CONSOLIDATE C3 / STRENGTHEN. line 129: `assert [a.attempt for a in results.task_results[0].attempts] == [1, 2, 3, 4]`.
- `test_workers_are_shared_across_tasks` at `tests/test_attempt_parallelism.py:132`. KEEP. line 148: `assert sum((len(t.attempts) for t in results.task_results)) == 6`.
- `test_fail_fast_keeps_a_task_sequential` at `tests/test_attempt_parallelism.py:151`. KEEP. line 168: `assert harness.peak_per_task == 1`.
- `test_every_attempt_runs_when_nothing_stops_the_run` at `tests/test_attempt_parallelism.py:172`. KEEP. line 188: `assert [len(t.attempts) for t in results.task_results] == [3, 3]`; line 189: `assert results.aggregate.avg_score == 1.0`; line 190: `assert results.run.interrupted is False`.
- `test_attempts_are_scheduled_round_robin_across_tasks` at `tests/test_attempt_parallelism.py:213`. KEEP. line 230: `assert harness.started == [('task-001', 1), ('task-002', 1), ('task-003', 1), ('task-001', 2), ('task-002', 2), ('task-003', 2)]`.

### test_cancel.py

- `test_cancelling_keeps_the_attempts_already_paid_for` at `tests/test_cancel.py:153`. KEEP. line 167: `assert [a.attempt for a in attempts] == [1]`; line 168: `assert attempts[0].outcome == Outcome.PASS`; line 169: `assert results.run.interrupted is True`; line 171: `assert results.task_results[0].score == 1.0`.
- `test_a_cancelled_run_does_not_start_the_remaining_attempts` at `tests/test_cancel.py:174`. KEEP. line 188: `assert harness.started == [1]`.
- `test_attempts_killed_by_the_cancellation_are_not_recorded` at `tests/test_cancel.py:214`. KEEP. line 230: `assert harness.started == [1]`; line 231: `assert results.task_results[0].attempts == []`; line 232: `assert results.run.interrupted is True`.
- `test_an_attempt_that_failed_on_its_own_is_kept` at `tests/test_cancel.py:235`. KEEP. line 253: `assert [a.outcome for a in attempts] == [Outcome.INFRA_ERROR]`; line 254: `assert results.task_results[0].unusable == 1`; line 255: `assert results.run.interrupted is True`.
- `test_a_fatal_error_mid_run_salvages_the_attempts_that_ran` at `tests/test_cancel.py:258`. KEEP. line 259: `pytest.raises(RunAborted)`; line 271: `assert isinstance(aborted.cause, HarnessConfigurationError)`; line 272: `assert 'credentials expired' in str(aborted.cause)`; line 273: `assert [a.attempt for a in aborted.results.task_results[0].attempts] == [1]`; line 274: `assert aborted.results.run.interrupted is True`.
- `test_an_unavailable_judge_model_stops_the_run_at_the_first_attempt` at `tests/test_cancel.py:288`. KEEP. line 294: `pytest.raises(RunAborted)`; line 306: `assert 'claude-bogus' in str(aborted.cause)`; line 307: `assert harness.started == [1]`; line 308: `assert aborted.results.task_results[0].attempts == []`.
- `test_cancel_kills_an_agent_in_flight` at `tests/test_cancel.py:311`. KEEP. line 336: `assert finished.wait(timeout=10)`; line 338: `assert time.monotonic() - started < 10`; line 339: `assert box['result'].returncode != 0`.
- `test_cancelling_a_finished_agent_with_exited_tools_keeps_its_attempt` at `tests/test_cancel.py:342`. KEEP. line 354: `assert cancel._descendants[proc]`; line 356: `assert not cancel.was_killed(proc)`.
- `test_killed_process_does_not_match_reused_pid` at `tests/test_cancel.py:360`. KEEP. line 369: `assert cancel.was_killed(proc)`; line 370: `assert not cancel.was_killed(SimpleNamespace(pid=proc.pid))`.
- `test_stopping_an_attempt_kills_tools_in_their_own_process_groups` at `tests/test_cancel.py:429`. KEEP. line 454: `assert all((_pid_alive(pid) for pid in pids))`; line 457: `assert finished.wait(5)`; line 458: `assert box['result'].timed_out is (stop == 'timeout')`; line 462: `assert not any((_pid_alive(pid) for pid in pids))`.
- `test_stopping_an_attempt_reaches_a_tool_after_its_agent_exits` at `tests/test_cancel.py:468`. KEEP. line 508: `assert not _pid_alive(agent_pid)`; line 509: `assert _pid_alive(tool_pid)`; line 510: `assert not leaf_pid_file.exists()`; line 513: `assert leaf_pid_text`; line 516: `assert _pid_alive(leaf_pid)`; line 519: `assert finished.wait(5)`; line 520: `assert box['result'].timed_out is (stop == 'timeout')`; line 524: `assert not any((_pid_alive(pid) for pid in pids))`.
- `test_stopping_does_not_hang_on_an_unidentifiable_pipe_holder` at `tests/test_cancel.py:530`. KEEP. line 553: `assert _pid_alive(pids[1])`; line 556: `assert finished.wait(4)`; line 557: `assert box['result'].timed_out is (stop == 'timeout')`; line 558: `assert box['result'].cancelled is (stop == 'cancel')`; line 561: `assert len(set(threading.enumerate()) - threads_before) <= 1`.
- `test_large_prompt_reaches_agent_after_a_slow_stdin_start` at `tests/test_cancel.py:564`. KEEP. line 578: `assert result.returncode == 0`; line 579: `assert result.stdout == str(len(prompt))`.
- `test_prompt_stdin_works_from_read_only_workdir` at `tests/test_cancel.py:583`. KEEP. line 596: `assert result.returncode == 0`; line 597: `assert result.stdout == 'prompt'`.
- `test_prompt_is_staged_in_the_workdir_unless_it_is_full` at `tests/test_cancel.py:626`. KEEP. line 622: `assert result.stdout == 'prompt'`; line 623: `assert staged_in == ([str(tmp_path), None] if workdir_full else [str(tmp_path)])`.
- `test_timeout_keeps_partial_output_when_a_pipe_holder_survives` at `tests/test_cancel.py:626`. KEEP. line 646: `assert finished.wait(4)`; line 647: `assert box['result'].timed_out`; line 648: `assert box['result'].stdout == 'partial transcript'`.
- `test_run_cli_exits_130_and_saves_an_interrupted_run` at `tests/test_cancel.py:680`. KEEP. line 700: `assert result.exit_code == 130`; line 701: `assert 'interrupted' in result.stdout`; line 703: `assert len(saved) == 1`.
- `test_run_cli_saves_before_reporting_a_fatal_error` at `tests/test_cancel.py:706`. KEEP. line 728: `assert result.exit_code == 2`; line 729: `assert 'credentials expired' in result.stdout`; line 731: `assert len(saved) == 1`.

### test_judge.py

- `test_eval_judge_always_returns_eval_judge_instance` at `tests/test_judge.py:82`. SAFE DELETE D1. line 85: `assert isinstance(judge, EvalJudge)`.
- `test_eval_judge_expect_only_calls_llm` at `tests/test_judge.py:88`. KEEP. line 98: `assert result.passed is True`; line 99: `assert result.assert_passed is None`; line 100: `assert result.autorater_passed is True`; line 101: `assert result.resolved_model == 'test-model'`.
- `test_judge_input_can_be_rebuilt_from_the_saved_transcript` at `tests/test_judge.py:104`. KEEP. line 123: `assert backend.prompts == [render_judge_prompt('writes a.txt', saved)]`.
- `test_prompt_version_moves_when_transcript_formatting_changes` at `tests/test_judge.py:126`. KEEP. line 130: `assert eval_judge._prompt_version() == eval_judge.JUDGE_PROMPT_VERSION`; line 138: `assert eval_judge._prompt_version() != eval_judge.JUDGE_PROMPT_VERSION`.
- `test_script_mode_keeps_the_autorater_script` at `tests/test_judge.py:147`. KEEP. line 153: `assert result.autorater_script == code`.
- `test_direct_verdict_has_no_autorater_script` at `tests/test_judge.py:156`. KEEP. line 161: `assert result.autorater_script is None`.
- `test_eval_judge_assert_only_runs_script_no_llm` at `tests/test_judge.py:164`. KEEP. line 172: `assert result.passed is True`; line 173: `assert result.assert_passed is True`; line 174: `assert result.autorater_passed is None`.
- `test_eval_judge_assert_failure_makes_overall_fail` at `tests/test_judge.py:177`. KEEP. line 187: `assert result.passed is False`; line 188: `assert result.assert_passed is False`.
- `test_eval_judge_both_checks_must_pass` at `tests/test_judge.py:191`. KEEP. line 199: `assert result.passed is False`; line 200: `assert result.autorater_passed is True`; line 201: `assert result.assert_passed is False`.
- `test_errored_autorater_dropped_when_assert_passes` at `tests/test_judge.py:204`. KEEP. line 212: `assert result.errored is False`; line 213: `assert result.passed is True`; line 214: `assert result.autorater_passed is None`.
- `test_errored_autorater_does_not_override_failing_assert` at `tests/test_judge.py:217`. KEEP. line 224: `assert result.errored is False`; line 225: `assert result.passed is False`.
- `test_judge_error_when_only_check_errors` at `tests/test_judge.py:228`. KEEP. line 236: `assert result.errored is True`; line 237: `assert result.passed is False`.
- `test_malformed_judge_answer_is_no_verdict` at `tests/test_judge.py:255`. KEEP. line 260: `assert (result.passed, result.errored) == (False, True)`; line 261: `assert result.autorater_passed is None`.
- `test_static_assert_survives_a_malformed_judge_answer` at `tests/test_judge.py:265`. KEEP. line 274: `assert (result.passed, result.errored) == (True, False)`; line 275: `assert result.autorater_passed is None`.
- `test_verdict_without_a_mode_is_still_a_verdict` at `tests/test_judge.py:278`. KEEP. line 283: `assert (result.passed, result.errored) == (True, False)`.
- `test_unknown_judge_backend_errors` at `tests/test_judge.py:286`. KEEP. line 293: `assert result.errored is True`; line 294: `assert 'Unknown judge backend' in result.autorater_reasoning`.
- `test_eval_judge_claude_code_invokes_claude_cli` at `tests/test_judge.py:300`. KEEP. line 314: `assert result.passed is True`; line 315: `assert result.autorater_passed is True`; line 316: `assert result.autorater_reasoning == 'The transcript says hello.'`; line 319: `assert cmd[:2] == ['claude', '-p']`; line 320: `assert '--output-format' in cmd`; line 322: `assert '--strict-mcp-config' in cmd`; line 323: `assert cmd[cmd.index('--model') + 1] == 'claude-test'`; line 324: `assert 'The assistant says hello.' in cmd[2]`; line 326: `assert 0 < kwargs['timeout'] <= 60`.
- `test_eval_judge_claude_code_defers_to_cli_default_model` at `tests/test_judge.py:329`. KEEP. line 344: `assert '--model' not in cmd`.
- `test_claude_judge_extracts_concrete_model_from_envelope` at `tests/test_judge.py:347`. KEEP. line 365: `assert result.passed is True`; line 366: `assert result.autorater_passed is True`; line 367: `assert result.resolved_model == 'claude-opus-4-8'`.
- `test_codex_judge_uses_output_last_message` at `tests/test_judge.py:381`. KEEP. line 411: `assert result.passed is True`; line 412: `assert result.errored is False`; line 413: `assert result.autorater_reasoning == 'The transcript matches.'`; line 415: `assert cmd[:2] == ['codex.cmd', 'exec']`; line 416: `assert cmd[cmd.index('--model') + 1] == 'test-model'`; line 417: `assert cmd[-1] == '-'`; line 420: `assert {'features.apps=false', 'features.plugins=false'} <= set(overrides)`; line 421: `assert 'Respond with valid JSON only' in kwargs['input']`; line 422: `assert kwargs['cwd'] == attempt_workdir.path`.
- `test_codex_missing_cli_errors` at `tests/test_judge.py:425`. KEEP. line 434: `assert result.error is not None`; line 435: `assert 'codex CLI not found' in result.error`.
- `test_codex_judge_timeout_removes_its_output_file` at `tests/test_judge.py:438`. KEEP. line 458: `assert result.error is not None`; line 459: `assert 'timed out' in result.error`; line 460: `assert staged`; line 461: `assert not staged[0].exists()`.
- `test_codex_judge_with_a_lapsed_login_stops_the_run` at `tests/test_judge.py:464`. KEEP. line 476: `pytest.raises(LoginRequired)`; line 480: `assert message.startswith('The codex judge cannot run.')`; line 481: `assert 'Your access token could not be refreshed.' in message`; line 482: `assert 'Run `codex.cmd login`' in message`; line 484: `assert exc.value.command == ['codex.cmd', 'login']`.
- `test_codex_judge_prompt_echoed_on_stderr_is_not_a_login` at `tests/test_judge.py:487`. KEEP. line 505: `assert result.error == 'codex judge failed: stream disconnected before completion'`.
- `test_codex_judge_crash_does_not_read_the_echoed_prompt` at `tests/test_judge.py:508`. KEEP. line 522: `assert result.error == 'codex judge exited 101'`.
- `test_codex_judge_refused_model_is_not_a_login` at `tests/test_judge.py:525`. KEEP. line 537: `pytest.raises(HarnessConfigurationError)`; line 541: `assert 'Codex CLI cannot run the requested model' in message`; line 542: `assert 'codex login' not in message`; line 543: `assert 'Pass `--judge-model codex` (no model)' in message`; line 544: `assert '`--model codex`' not in message`.
- `test_a_cancelled_judge_call_is_not_a_verdict` at `tests/test_judge.py:547`. KEEP. line 556: `pytest.raises(StepCancelled)`.
- `test_codex_error_extraction_from_noisy_cli_output` at `tests/test_judge.py:560`. KEEP. line 567: `assert _extract_codex_error(output) == "codex judge failed: The 'bad-model' model is not supported when using Codex with a ChatGPT account."`.
- `test_hermes_backend_is_a_valid_judge` at `tests/test_judge.py:583`. KEEP. line 598: `assert result.passed is True`; line 599: `assert result.autorater_passed is True`; line 601: `assert cmd[0] == '/usr/bin/hermes'`; line 602: `assert '-z' in cmd`; line 603: `assert '--ignore-rules' in cmd`; line 604: `assert cmd[cmd.index('--model') + 1] == 'anthropic/claude-sonnet-4.6'`.
- `test_judge_strips_markdown_fence` at `tests/test_judge.py:607`. KEEP. line 620: `assert (result.passed, result.errored) == (False, False)`; line 621: `assert result.autorater_reasoning == 'no'`.
- `test_hermes_judge_reports_cli_error_as_errored` at `tests/test_judge.py:624`. KEEP. line 630: `assert result.error is not None`; line 631: `assert 'connection reset by peer' in result.error`.
- `test_hermes_judge_missing_cli_errors` at `tests/test_judge.py:634`. KEEP. line 640: `assert result.error is not None`; line 641: `assert 'hermes CLI not found' in result.error`.
- `test_pi_backend_is_a_valid_judge` at `tests/test_judge.py:660`. KEEP. line 675: `assert result.passed is True`; line 676: `assert result.autorater_passed is True`; line 679: `assert cmd[cmd.index('--model') + 1] == 'claude-sonnet-4-6'`; line 680: `assert '--print' in cmd`; line 681: `assert '--mode' in cmd`.
- `test_pi_judge_reports_cli_error_as_errored` at `tests/test_judge.py:684`. KEEP. line 691: `assert result.error is not None`; line 692: `assert 'connection reset by peer' in result.error`.
- `test_pi_judge_with_a_rejected_key_stops_the_run` at `tests/test_judge.py:695`. KEEP. line 714: `pytest.raises(LoginRequired)`; line 718: `assert message.startswith('The pi judge cannot run.')`; line 719: `assert 'API key is invalid.' in message`; line 720: `assert '`pi` then `/login`' in message`; line 722: `assert exc.value.command is None`.
- `test_a_judge_answer_about_a_login_failure_is_a_verdict` at `tests/test_judge.py:725`. KEEP. line 744: `assert (result.passed, result.errored) == (False, False)`; line 745: `assert result.autorater_reasoning == 'The agent stopped at a 401: not logged in.'`.
- `test_pi_judge_missing_cli_errors` at `tests/test_judge.py:748`. KEEP. line 754: `assert result.error is not None`; line 755: `assert 'pi CLI not found' in result.error`.
- `test_assert_script_file_resolves_from_spec_dir_and_runs_in_workdir` at `tests/test_judge.py:758`. KEEP. line 774: `assert result.assert_passed is True`.
- `test_autorater_runs_in_the_attempt_workdir_not_the_spec_dir` at `tests/test_judge.py:777`. KEEP. line 793: `assert kwargs['cwd'] == attempt_workdir.path`.
- `test_an_assertion_that_hangs_has_no_verdict` at `tests/test_judge.py:796`. KEEP. line 805: `assert result.errored is True`; line 806: `assert result.assert_passed is None`; line 807: `assert result.assert_evidence == 'assert timed out after 1s'`.
- `test_the_autorater_times_its_own_call` at `tests/test_judge.py:813`. STRENGTHEN 3. line 820: `assert result.autorater_seconds is not None`; line 821: `assert result.autorater_seconds >= 0`.
- `test_an_assert_script_beside_the_autorater_is_not_judge_time` at `tests/test_judge.py:824`. KEEP. line 833: `assert result.assert_passed is True`; line 834: `assert result.autorater_seconds < 0.3`.
- `test_an_assert_only_task_records_no_autorater_time` at `tests/test_judge.py:837`. KEEP. line 845: `assert result.autorater_seconds is None`; line 846: `assert harness.calls == 0`.
- `test_an_unavailable_judge_model_stops_the_run` at `tests/test_judge.py:849`. KEEP. line 861: `pytest.raises(HarnessConfigurationError)`.

### test_runner.py

- `test_failed_setup_cannot_pass_from_stale_artifact_and_still_cleans_up` at `tests/test_runner.py:65`. KEEP. line 91: `assert harness.calls == 0`; line 92: `assert record.outcome is Outcome.INFRA_ERROR`; line 93: `assert record.passed is False`; line 94: `assert record.assert_passed is None`; line 95: `assert results.task_results[0].score is None`; line 96: `assert cleaned.exists()`; line 97: `assert [(f.phase, f.exit_code, f.output) for f in record.hook_failures] == [('setup', 7, 'setup broke')]`; line 100: `assert results.run.hook_failures == record.hook_failures`.
- `test_failed_setup_with_markup_output_still_reports` at `tests/test_runner.py:103`. KEEP. line 122: `assert 'setup hook exited 7' in output`; line 124: `assert '[/broken]' in output`.
- `test_cancelling_setup_skips_agent_and_still_runs_cleanup` at `tests/test_runner.py:128`. KEEP. line 158: `assert started.exists()`; line 161: `assert not thread.is_alive()`; line 162: `assert cleaned.exists()`; line 163: `assert harness.calls == 0`; line 164: `assert outcome['results'].run.interrupted is True`; line 165: `assert outcome['results'].task_results[0].attempts == []`.
- `test_failed_cleanup_keeps_agent_outcome_and_reports_both_hook_failures` at `tests/test_runner.py:172`. KEEP. line 191: `assert results.task_results[0].attempts[0].outcome is Outcome.INFRA_ERROR`; line 192: `assert [(f.phase, f.exit_code) for f in results.run.hook_failures] == [('setup', 7), ('cleanup', 9)]`; line 207: `assert record.outcome is Outcome.PASS`; line 208: `assert record.hook_failures[0].phase == 'cleanup'`; line 209: `assert passed.run.hook_failures == record.hook_failures`.
- `test_cleanup_runs_after_failed_timed_out_or_cancelled_agent` at `tests/test_runner.py:220`. KEEP. line 241: `assert cleaned.exists()`; line 242: `assert results.run.hook_failures[0].phase == 'cleanup'`; line 243: `assert results.run.hook_failures[0].exit_code == 9`; line 246: `assert records == []`; line 248: `assert records[0].outcome is expected_outcome`; line 249: `assert records[0].hook_failures == results.run.hook_failures`.
- `test_runner_fails_attempt_when_harness_exits_nonzero` at `tests/test_runner.py:280`. KEEP. line 299: `assert attempt.outcome is Outcome.INFRA_ERROR`; line 300: `assert attempt.passed is False`; line 301: `assert attempt.assert_evidence == 'agent failed'`; line 303: `assert tr.unusable == 1`; line 304: `assert tr.pass_at_k is None`; line 305: `assert judge.calls == 0`.
- `test_runner_runs_all_infra_failures_by_default` at `tests/test_runner.py:308`. KEEP. line 323: `assert harness.attempts == [1, 2, 3]`; line 324: `assert len(results.task_results[0].attempts) == 3`; line 325: `assert results.task_results[0].unusable == 3`; line 326: `assert results.task_results[0].pass_at_k is None`.
- `test_runner_fail_fast_stops_after_unusable_threshold` at `tests/test_runner.py:329`. KEEP. line 346: `assert harness.attempts == [1]`; line 347: `assert [attempt.outcome for attempt in task.attempts] == [Outcome.INFRA_ERROR]`; line 348: `assert task.unusable == 1`; line 349: `assert task.pass_at_k is None`.
- `test_runner_fail_fast_does_not_reset_streak_on_judge_error` at `tests/test_runner.py:352`. KEEP. line 369: `assert harness.attempts == [1, 2, 3]`; line 370: `assert [attempt.outcome for attempt in task.attempts] == [Outcome.INFRA_ERROR, Outcome.JUDGE_ERROR, Outcome.INFRA_ERROR]`; line 375: `assert task.unusable == 3`; line 376: `assert task.pass_at_k is None`.
- `test_runner_emits_task_done_when_fail_fast_stops_early` at `tests/test_runner.py:379`. KEEP. line 396: `assert len(finished_tasks) == 1`; line 397: `assert finished_tasks[0].task_id == 'task-001'`; line 398: `assert len(finished_tasks[0].attempts) == 1`; line 399: `assert finished_tasks[0].pass_at_k is None`.
- `test_runmeta_records_judge_engine_and_resolved_model` at `tests/test_runner.py:417`. KEEP. line 439: `assert results.run.judge_backend == 'hermes'`; line 440: `assert results.run.judge_model == 'anthropic/claude-sonnet-4.6'`; line 442: `assert results.run.model == 'stepfun/step-3.7-flash:free'`.
- `test_runmeta_fills_default_judge_model_from_autorater` at `tests/test_runner.py:445`. KEEP. line 460: `assert results.run.judge_backend == 'claude-code'`; line 461: `assert results.run.judge_model == 'claude-opus-4-8'`.
- `test_runmeta_records_the_model_a_named_judge_resolved_to` at `tests/test_runner.py:464`. KEEP. line 485: `assert results.run.judge_model == 'claude-opus-5-5'`.
- `test_runmeta_records_the_most_common_judge_model_and_warns` at `tests/test_runner.py:502`. KEEP. line 519: `assert results.run.judge_model == 'model-a'`; line 520: `assert any((w.startswith('Judge: ') and 'model-b' in w for w in warnings))`.
- `test_runmeta_records_no_judge_model_when_no_autorater_ran` at `tests/test_runner.py:523`. KEEP. line 543: `assert results.run.judge_backend == 'claude-code'`; line 544: `assert results.run.judge_model is None`; line 545: `assert results.run.judge_prompt_version is None`.
- `test_run_records_what_rebuilds_the_judge_input` at `tests/test_runner.py:555`. KEEP. line 573: `assert results.run.judge_prompt_version == JUDGE_PROMPT_VERSION`; line 574: `assert results.task_results[0].expect == 'says hello'`.
- `test_runmeta_records_resolved_model_over_requested_and_warns` at `tests/test_runner.py:577`. KEEP. line 602: `assert results.run.model == 'some/other-model'`; line 603: `assert len(warnings) == 1`; line 604: `assert 'anthropic/claude-sonnet-4.6' in warnings[0]`; line 605: `assert 'some/other-model' in warnings[0]`.
- `test_runmeta_records_the_majority_model_and_warns_on_a_mixed_run` at `tests/test_runner.py:608`. KEEP. line 639: `assert results.run.model == 'provider/model-a'`; line 640: `assert len(warnings) == 1`; line 641: `assert 'provider/model-a' in warnings[0]`; line 642: `assert 'provider/model-b' in warnings[0]`.
- `test_runmeta_ignores_the_models_of_cancelled_attempts` at `tests/test_runner.py:645`. KEEP. line 671: `assert results.run.model == 'provider/model-b'`.
- `test_runner_persists_attempt_transcript` at `tests/test_runner.py:674`. KEEP. line 689: `assert attempt.transcript is not None`; line 690: `assert len(attempt.transcript) == 3`; line 691: `assert attempt.transcript[1].tool_name == 'mcp__wiki__read'`; line 692: `assert attempt.transcript[1].tool_input == {'page': 'home'}`; line 693: `assert attempt.transcript[2].tool_output == 'ok'`.
- `test_agent_setup_and_assert_share_one_attempt_workdir` at `tests/test_runner.py:720`. KEEP. line 758: `assert harness.saw_setup_marker is True`; line 759: `assert record.outcome is Outcome.PASS`; line 760: `assert record.hook_failures == []`; line 761: `assert list(launch_dir.iterdir()) == []`; line 762: `assert sorted((p.name for p in spec_dir.iterdir())) == ['fixture.txt']`; line 764: `assert harness.workdir is not None`; line 765: `assert not Path(harness.workdir).exists()`.
- `test_hooks_see_the_workdir_and_spec_dir_env` at `tests/test_runner.py:768`. KEEP. line 789: `assert Path(pwd).resolve() == Path(workdir).resolve()`; line 790: `assert workdir == harness.workdir`; line 791: `assert Path(spec_dir) == tmp_path`.
- `test_spec_dir_env_is_absolute_for_a_relative_spec_path` at `tests/test_runner.py:794`. KEEP. line 825: `assert record.hook_failures == []`; line 826: `assert record.outcome is Outcome.PASS`.
- `test_a_hanging_setup_is_an_infra_error_not_a_stuck_worker` at `tests/test_runner.py:830`. KEEP. line 852: `assert harness.calls == 0`; line 853: `assert record.outcome is Outcome.INFRA_ERROR`; line 854: `assert record.assert_evidence == 'setup timed out after 1s'`; line 855: `assert record.hook_failures[0].output == '[caliper: setup timed out after 1s]'`.
- `test_tasks_built_without_ids_keep_their_own_attempts` at `tests/test_runner.py:858`. KEEP. line 881: `assert outcomes == {'passes': [Outcome.PASS], 'fails': [Outcome.TASK_FAIL]}`.
- `test_run_refuses_fewer_than_one_attempt` at `tests/test_runner.py:885`. KEEP. line 886: `pytest.raises(ValueError, match='k must be at least 1')`.

### test_run_seam.py

- `test_the_backends_own_model_answers_a_request_that_names_none` at `tests/test_run_seam.py:55`. KEEP. line 68: `assert harness.seen[0].model == 'backend-default'`; line 71: `assert caller_ctx.model is None`.
- `test_a_named_model_is_not_overridden_by_the_backends_own` at `tests/test_run_seam.py:74`. KEEP. line 82: `assert harness.seen[0].model == 'asked-for'`.
- `test_an_empty_model_falls_back_like_an_absent_one` at `tests/test_run_seam.py:85`. KEEP. line 92: `assert harness.seen[0].model == 'backend-default'`.
- `test_each_invocation_gets_its_own_context` at `tests/test_run_seam.py:95`. STRENGTHEN 5. line 110: `assert harness.seen[0] is not harness.seen[1]`; line 111: `assert [ctx.attempt for ctx in harness.seen] == [1, 2]`.
- `test_the_context_owns_its_lists` at `tests/test_run_seam.py:114`. KEEP. line 140: `assert neighbourhood == []`; line 141: `assert forbidden == ['answer.md']`.

### test_retry.py

- `test_a_throttled_invocation_is_retried_until_it_lands` at `tests/test_retry.py:98`. KEEP. line 103: `assert len(calls) == 3`; line 104: `assert invoked.retries == 2`; line 105: `assert invoked.result.final_output == 'done'`.
- `test_retries_are_bounded` at `tests/test_retry.py:108`. KEEP. line 113: `assert len(calls) == 3`; line 114: `assert invoked.retries == 2`; line 117: `assert invoked.result.exit_code == 1`.
- `test_a_clean_invocation_is_never_retried` at `tests/test_retry.py:120`. KEEP. line 125: `assert len(calls) == 1`; line 126: `assert invoked.retries == 0`.
- `test_a_timeout_is_not_retried` at `tests/test_retry.py:129`. KEEP. line 135: `assert len(calls) == 1`.
- `test_a_bare_crash_is_not_retried` at `tests/test_retry.py:138`. KEEP. line 144: `assert len(calls) == 1`.
- `test_a_spending_cap_raises_instead_of_retrying` at `tests/test_retry.py:147`. KEEP. line 150: `pytest.raises(SpendingCapReached)`; line 153: `assert len(calls) == 1`; line 154: `assert 'usage limit' in str(excinfo.value)`.
- `test_a_cancelled_backoff_stops_retrying` at `tests/test_retry.py:157`. KEEP. line 164: `assert len(calls) == 1`; line 165: `assert invoked.retries == 0`.
- `test_backoff_grows_and_is_jittered` at `tests/test_retry.py:168`. KEEP. line 174: `assert all((2.0 <= d <= 2.5 for d in first))`; line 175: `assert all((4.0 <= d <= 5.0 for d in second))`; line 177: `assert len(set(first)) > 1`.
- `test_merge_keeps_the_last_invocations_answer` at `tests/test_retry.py:180`. KEEP. line 183: `assert merged.final_output == 'the answer'`; line 184: `assert merged.transcript[-1].content == 'the answer'`.
- `test_merge_sums_cost_and_spawn_time_but_not_the_waiting` at `tests/test_retry.py:187`. KEEP. line 196: `assert merged.duration_seconds == 8.0`; line 197: `assert merged.usage.input_tokens == 30`; line 198: `assert merged.usage.output_tokens == 3`.
- `test_merge_leaves_usage_none_when_no_invocation_reported_any` at `tests/test_retry.py:201`. KEEP. line 205: `assert merged.usage is None`.
- `test_a_retried_attempt_is_one_attempt_in_the_results` at `tests/test_retry.py:256`. KEEP. line 272: `assert harness.invocations == 6`; line 273: `assert [a.attempt for a in attempts] == [1, 2, 3]`; line 274: `assert [a.outcome for a in attempts] == [Outcome.PASS] * 3`; line 275: `assert [a.retries for a in attempts] == [1, 1, 1]`; line 277: `assert results.task_results[0].score == 1.0`.
- `test_a_spending_cap_aborts_the_run_and_saves_what_ran` at `tests/test_retry.py:280`. STRENGTHEN 2. line 285: `pytest.raises(RunAborted)`; line 297: `assert isinstance(aborted.cause, SpendingCapReached)`; line 298: `assert aborted.results.run.interrupted is True`; line 301: `assert aborted.results.task_results[0].attempts == []`.
- `test_fail_fast_counts_attempts_not_invocations` at `tests/test_retry.py:304`. KEEP. line 321: `assert [a.outcome for a in attempts] == [Outcome.INFRA_ERROR] * 2`; line 322: `assert [a.retries for a in attempts] == [2, 2]`; line 325: `assert harness.calls == 6`.
- `test_a_run_that_measured_nothing_is_not_saved` at `tests/test_retry.py:328`. KEEP. line 349: `assert result.exit_code == 2`; line 350: `assert 'Spending cap' in result.stdout`; line 351: `assert not (tmp_path / '.caliper').exists()`.
- `test_a_passing_attempt_that_mentions_a_cap_does_not_abort_the_run` at `tests/test_retry.py:370`. KEEP. line 375: `assert len(calls) == 1`; line 376: `assert invoked.retries == 0`; line 377: `assert invoked.result.final_output == ANSWER_ABOUT_LIMITS`.
- `test_a_passing_attempt_that_mentions_a_throttle_is_not_respawned` at `tests/test_retry.py:380`. SAFE DELETE D3. line 386: `assert len(calls) == 1`.
- `test_a_cap_on_a_zero_exit_still_stops_the_run` at `tests/test_retry.py:389`. SAFE DELETE D2. line 393: `pytest.raises(SpendingCapReached)`.
- `test_a_timeout_carrying_throttle_text_is_still_not_retried` at `tests/test_retry.py:397`. KEEP. line 411: `assert len(calls) == 1`; line 412: `assert invoked.result.timed_out is True`.
- `test_each_invocation_of_an_attempt_gets_its_own_context` at `tests/test_retry.py:415`. KEEP. line 440: `assert first is not second`; line 442: `assert (first.task_id, first.attempt) == (second.task_id, second.attempt)`.

### test_measure_and_mark.py

- `test_judge_time_is_recorded_on_a_graded_attempt` at `tests/test_measure_and_mark.py:81`. KEEP. line 88: `assert record.judge_seconds == 1.5`.
- `test_judge_time_is_none_when_no_judge_ran` at `tests/test_measure_and_mark.py:91`. KEEP. line 99: `assert record.outcome == Outcome.NOT_CHECKED`; line 100: `assert record.judge_seconds is None`.
- `test_judge_time_is_none_for_an_assert_only_task` at `tests/test_measure_and_mark.py:103`. KEEP. line 111: `assert record.outcome == Outcome.PASS`; line 112: `assert record.judge_seconds is None`.
- `test_judge_time_is_none_when_the_attempt_never_reached_the_judge` at `tests/test_measure_and_mark.py:115`. KEEP. line 120: `assert record.outcome == Outcome.TIMEOUT`; line 121: `assert record.judge_seconds is None`.
- `test_usage_totals_average_judge_time_over_graded_attempts_only` at `tests/test_measure_and_mark.py:141`. KEEP. line 145: `assert totals.judge_seconds == 6.0`; line 146: `assert totals.judged_attempts == 2`; line 147: `assert totals.attempts == 3`.
- `test_usage_totals_report_no_judge_when_nothing_was_graded` at `tests/test_measure_and_mark.py:150`. KEEP. line 153: `assert totals.judged_attempts == 0`; line 154: `assert totals.judge_seconds == 0.0`.
- `test_compare_warns_when_a_side_was_interrupted` at `tests/test_measure_and_mark.py:187`. KEEP. line 190: `assert any(('stopped before every attempt ran' in w for w in comp.warnings))`; line 191: `assert any((w.startswith('A ') for w in comp.warnings))`.
- `test_compare_names_both_sides_when_both_were_interrupted` at `tests/test_measure_and_mark.py:194`. KEEP. line 198: `assert warning.startswith('A and B')`.
- `test_compare_is_silent_on_two_complete_runs` at `tests/test_measure_and_mark.py:201`. KEEP. line 205: `assert not any(('stopped before' in w for w in comp.warnings))`.
- `test_list_marks_an_interrupted_run` at `tests/test_measure_and_mark.py:214`. KEEP. line 220: `assert result.exit_code == 0`; line 221: `assert '⊘' in result.stdout`; line 222: `assert 'stopped early' in result.stdout`.
- `test_list_is_unmarked_when_every_run_completed` at `tests/test_measure_and_mark.py:225`. KEEP. line 233: `assert result.exit_code == 0`; line 234: `assert '2026-07-03T10-00-00Z' in result.stdout`; line 235: `assert 'stopped early' not in result.stdout`.
- `test_compare_reports_a_regression_without_failing` at `tests/test_measure_and_mark.py:238`. STRENGTHEN 4. line 251: `assert result.exit_code == 0`.

### test_ablation.py

- `test_ablate_installs_the_neighbourhood_minus_the_named_skill` at `tests/test_ablation.py:147`. KEEP. line 151: `assert _installed(harness) == [['keeper']]`.
- `test_ablating_every_member_leaves_the_bare_agent` at `tests/test_ablation.py:154`. KEEP. line 158: `assert _installed(harness) == [[]]`.
- `test_a_run_without_ablate_installs_everything` at `tests/test_ablation.py:161`. KEEP. line 165: `assert _installed(harness) == [['subject', 'keeper']]`; line 166: `assert results.run.ablated == []`.
- `test_the_ablated_names_are_recorded_on_run_meta` at `tests/test_ablation.py:172`. CONSOLIDATE C4. line 177: `assert results.run.ablated == ['subject']`; line 178: `assert results.run.ablated_skills == ['subject']`; line 179: `assert results.run.ablated_servers == []`.
- `test_snapshots_cover_only_the_installed_skills` at `tests/test_ablation.py:182`. CONSOLIDATE C4. line 187: `assert [s.name for s in results.skill_snapshots] == ['keeper']`.
- `test_a_repeated_name_is_recorded_once` at `tests/test_ablation.py:190`. KEEP. line 195: `assert results.run.ablated == ['subject']`.
- `test_ablating_an_undeclared_skill_is_refused` at `tests/test_ablation.py:198`. KEEP. line 201: `pytest.raises(SkillResolutionError)`; line 203: `assert 'subjekt' in str(exc.value)`.
- `test_ablate_removes_a_declared_mcp_server` at `tests/test_ablation.py:209`. KEEP. line 218: `assert _servers(harness) == [{}]`; line 219: `assert _installed(harness) == [['subject']]`.
- `test_a_run_without_ablate_hands_over_every_server` at `tests/test_ablation.py:222`. KEEP. line 226: `assert _servers(harness) == [{'weather': McpServer(command='python3', args=['w.py'])}]`.
- `test_the_ablated_server_is_recorded_qualified_on_run_meta` at `tests/test_ablation.py:231`. CONSOLIDATE C5. line 236: `assert results.run.ablated == ['mcp:weather']`; line 237: `assert results.run.ablated_skills == []`; line 238: `assert results.run.ablated_servers == ['weather']`; line 239: `assert results.run.mcp_servers == []`.
- `test_the_servers_a_run_kept_are_recorded_on_run_meta` at `tests/test_ablation.py:242`. CONSOLIDATE C6. line 245: `assert results.run.ablated == []`; line 246: `assert results.run.mcp_servers == ['weather']`.
- `test_a_bare_name_on_a_collision_is_refused` at `tests/test_ablation.py:249`. KEEP. line 251: `pytest.raises(AblationError)`; line 254: `assert 'mcp:weather' in message`; line 255: `assert 'skill:weather' in message`; line 256: `assert exc.value.title == 'Invalid ablation'`.
- `test_the_mcp_qualifier_removes_the_server_not_the_skill` at `tests/test_ablation.py:259`. KEEP. line 263: `assert _installed(harness) == [['subject', 'weather']]`; line 264: `assert _servers(harness) == [{}]`; line 265: `assert results.run.ablated == ['mcp:weather']`.
- `test_the_skill_qualifier_removes_the_skill_not_the_server` at `tests/test_ablation.py:268`. KEEP. line 272: `assert _installed(harness) == [['subject']]`; line 273: `assert _servers(harness) == [{'weather': McpServer(command='python3', args=['w.py'])}]`; line 276: `assert results.run.ablated == ['weather']`.
- `test_ablating_an_undeclared_server_is_refused` at `tests/test_ablation.py:279`. KEEP. line 281: `pytest.raises(SkillResolutionError)`; line 283: `assert 'mcp:snow' in str(exc.value)`.
- `test_repeating_a_server_name_records_one_removal` at `tests/test_ablation.py:286`. KEEP. line 291: `assert results.run.ablated == ['mcp:weather']`.
- `test_ablating_a_server_leaves_the_activation_expectation_scored` at `tests/test_ablation.py:294`. KEEP. line 300: `assert task.activation_expected == ['subject']`; line 301: `assert task.activation_score == 1.0`.
- `test_an_ablated_run_drops_the_activation_expectation` at `tests/test_ablation.py:307`. CONSOLIDATE C7. line 314: `assert task.activation_expected is None`; line 315: `assert task.activation_score is None`.
- `test_an_activates_naming_the_ablated_skill_is_not_a_validation_error` at `tests/test_ablation.py:318`. KEEP. line 323: `assert results.task_results[0].activation_expected is None`.
- `test_the_observation_survives_even_though_the_verdict_does_not` at `tests/test_ablation.py:326`. KEEP. line 332: `assert attempt.activated == ['keeper']`; line 333: `assert attempt.activation_passed is None`.
- `test_a_normal_run_still_scores_its_activation_expectation` at `tests/test_ablation.py:336`. KEEP. line 339: `assert results.task_results[0].activation_expected == ['keeper']`; line 340: `assert results.task_results[0].activation_score == 1.0`.
- `test_an_ablation_pair_is_labelled_and_not_warned_about` at `tests/test_ablation.py:377`. KEEP. line 383: `assert comp.neighbourhood_mismatch is False`; line 384: `assert comp.warnings == []`; line 385: `assert comp.a_label == 'without subject'`; line 386: `assert comp.b_label == 'with subject'`.
- `test_a_bare_agent_pair_is_labelled_as_such` at `tests/test_ablation.py:389`. KEEP. line 393: `assert comp.a_label == 'without any skills or servers'`; line 394: `assert comp.b_label == 'with keeper, subject'`; line 395: `assert comp.neighbourhood_mismatch is False`.
- `test_the_ablated_side_is_recognised_in_either_position` at `tests/test_ablation.py:398`. KEEP. line 402: `assert comp.a_label == 'with subject'`; line 403: `assert comp.b_label == 'without subject'`; line 404: `assert comp.neighbourhood_mismatch is False`.
- `test_a_server_only_ablation_is_labelled_from_the_marker` at `tests/test_ablation.py:407`. KEEP. line 414: `assert comp.neighbourhood_mismatch is False`; line 415: `assert comp.warnings == []`; line 416: `assert comp.a_label == 'without mcp:weather'`; line 417: `assert comp.b_label == 'with mcp:weather'`.
- `test_ablating_a_server_is_not_a_bare_agent_when_a_server_survives` at `tests/test_ablation.py:420`. KEEP. line 426: `assert comp.a_label == 'without mcp:first'`; line 427: `assert comp.b_label == 'with mcp:first'`.
- `test_ablating_every_skill_is_not_a_bare_agent_when_a_server_survives` at `tests/test_ablation.py:430`. KEEP. line 435: `assert comp.a_label == 'without subject'`.
- `test_a_legacy_side_without_recorded_servers_is_not_called_bare` at `tests/test_ablation.py:438`. KEEP. line 445: `assert comp.a_label == 'without subject'`.
- `test_a_legacy_run_without_recorded_servers_still_pairs` at `tests/test_ablation.py:448`. KEEP. line 455: `assert comp.a_label == 'without subject'`; line 456: `assert comp.b_label == 'with subject'`.
- `test_a_server_marker_the_full_side_never_had_is_not_a_pair` at `tests/test_ablation.py:459`. KEEP. line 465: `assert comp.a_label is None`; line 466: `assert comp.b_label is None`; line 467: `assert comp.neighbourhood_mismatch is False`.
- `test_a_server_marker_the_ablated_side_still_ran_with_is_not_a_pair` at `tests/test_ablation.py:470`. KEEP. line 476: `assert comp.a_label is None`.
- `test_a_server_difference_between_two_skill_ablated_runs_is_not_a_pair` at `tests/test_ablation.py:479`. KEEP. line 485: `assert comp.a_label is None`.
- `test_different_recorded_servers_warn` at `tests/test_ablation.py:488`. KEEP. line 494: `assert comp.mcp_mismatch is True`; line 495: `assert any(('MCP servers' in w for w in comp.warnings))`; line 496: `assert comp.a_label is None`; line 497: `assert comp.b_label is None`.
- `test_matching_recorded_servers_do_not_warn` at `tests/test_ablation.py:500`. KEEP. line 504: `assert comp.mcp_mismatch is False`; line 505: `assert comp.warnings == []`.
- `test_an_unrecorded_membership_does_not_warn` at `tests/test_ablation.py:508`. KEEP. line 515: `assert comp.mcp_mismatch is False`; line 516: `assert comp.warnings == []`.
- `test_two_runs_that_ablated_different_skills_still_warn` at `tests/test_ablation.py:519`. KEEP. line 526: `assert comp.neighbourhood_mismatch is True`; line 527: `assert any(('neighbourhood' in w for w in comp.warnings))`; line 528: `assert comp.a_label is None`.
- `test_an_inconsistent_marker_is_not_treated_as_a_pair` at `tests/test_ablation.py:531`. KEEP. line 537: `assert comp.neighbourhood_mismatch is True`; line 538: `assert comp.a_label is None`.
- `test_two_full_runs_are_unlabelled` at `tests/test_ablation.py:541`. KEEP. line 545: `assert comp.a_label is None`; line 546: `assert comp.b_label is None`; line 547: `assert comp.warnings == []`.

### test_run_cli.py

- `test_run_cli_forwards_options_to_run` at `tests/test_run_cli.py:39`. KEEP. line 92: `assert result.exit_code == 0`; line 94: `assert calls['k'] == 3`; line 95: `assert calls['workers'] == 2`; line 96: `assert calls['fail_fast_unusable'] == 2`; line 98: `assert calls['timeout'] == 120`; line 99: `assert calls['ablate'] == []`; line 101: `assert calls['user_customizations'] is None`; line 103: `assert callable(calls['on_warning'])`.
- `test_run_cli_resolves_backend_and_judge_model_targets` at `tests/test_run_cli.py:106`. KEEP. line 163: `assert result.exit_code == 0`; line 164: `assert harness_args == {'backend': 'codex', 'model': 'gpt-5-codex'}`; line 165: `assert judge_args == {'backend': 'pi', 'model': 'claude-sonnet-4-6'}`.
- `test_run_cli_collects_repeated_ablate_flags` at `tests/test_run_cli.py:168`. KEEP. line 207: `assert result.exit_code == 0`; line 208: `assert calls['ablate'] == ['grilling', 'docs']`.
- `test_run_cli_user_customizations_notice` at `tests/test_run_cli.py:264`. KEEP. line 286: `assert result.exit_code == 0`; line 287: `assert calls['user_customizations'] is forwarded`; line 289: `assert shown in result.output`; line 291: `assert hidden not in result.output`.
- `test_run_cli_exits_two_after_cleanup_failure` at `tests/test_run_cli.py:294`. KEEP. line 318: `assert result.exit_code == 2`; line 319: `assert len(saved) == 1`; line 320: `assert saved[0][0].task_results[0].attempts[0].outcome is Outcome.PASS`.
- `test_run_cli_exits_two_when_no_attempt_was_usable` at `tests/test_run_cli.py:346`. KEEP. line 351: `assert result.exit_code == 2`; line 352: `assert len(saved) == 1`; line 353: `assert '2 infra_error' in result.output`; line 354: `assert '1 timeout' in result.output`.
- `test_run_cli_exits_zero_when_some_attempt_was_usable` at `tests/test_run_cli.py:357`. KEEP. line 362: `assert result.exit_code == 0`.
- `test_run_cli_exits_zero_for_a_trigger_probe_run` at `tests/test_run_cli.py:365`. KEEP. line 371: `assert result.exit_code == 0`.
- `test_report_highlights_cleanup_failure_on_passing_attempt` at `tests/test_run_cli.py:374`. KEEP. line 389: `assert '1 lifecycle hook failed' in output`; line 390: `assert 'cleanup hook exited 9' in output`; line 391: `assert 'cleanup broke' in output`; line 392: `assert '⊘ hook' in output`.
- `test_run_cli_saves_the_run_at_the_discovered_root` at `tests/test_run_cli.py:407`. KEEP. line 423: `assert result.exit_code == 0`; line 424: `assert not (spec_dir / '.caliper').exists()`; line 426: `assert saved is not None`.
- `test_run_cli_writes_output_to_a_new_directory` at `tests/test_run_cli.py:429`. KEEP. line 441: `assert result.exit_code == 0`; line 442: `assert RunResults.model_validate_json(output.read_text()) == finished`; line 443: `assert 'Overall' in result.output`; line 444: `assert 'Results saved to' in result.output`.
- `test_run_cli_keeps_report_and_interrupt_exit_if_output_cannot_be_written` at `tests/test_run_cli.py:447`. KEEP. line 463: `assert result.exit_code == 130`; line 464: `assert 'Could not write --output' in result.output`; line 465: `assert 'Overall' in result.output`; line 467: `assert 'Results saved to' in result.output`; line 469: `assert saved is not None`; line 470: `assert RunResults.model_validate_json(saved.read_text()).run.interrupted`.
- `test_a_run_is_findable_by_report_from_another_directory` at `tests/test_run_cli.py:473`. KEEP. line 489: `assert run_result.exit_code == 0`; line 493: `assert report_result.exit_code == 0`; line 494: `assert '2026-07-04T09:00:00' in report_result.output`.
- `test_report_outside_the_project_does_not_find_its_runs` at `tests/test_run_cli.py:497`. KEEP. line 510: `assert runner.invoke(app, ['run', 'evals/sample.eval.yaml']).exit_code == 0`; line 518: `assert result.exit_code == 1`; line 519: `assert 'No evaluation results' in result.output`.
- `test_baseline_is_no_longer_an_option` at `tests/test_run_cli.py:522`. KEEP, policy retirement option. line 531: `assert result.exit_code == 2`; line 532: `assert 'No such option' in result.output`.
- `test_run_cli_rejects_fewer_than_one_attempt` at `tests/test_run_cli.py:535`. KEEP. line 542: `assert result.exit_code == 1`; line 543: `assert '--k' in result.output`.
- `test_run_cli_refuses_unknown_backends_before_any_attempt` at `tests/test_run_cli.py:546`. KEEP. line 564: `assert result.exit_code == 2`; line 565: `assert 'Traceback' not in result.output`; line 566: `assert flag in result.output`; line 568: `assert 'Unknown backend' in result.output`; line 569: `assert 'claude-code' in result.output`; line 570: `assert 'codex' in result.output`.
- `test_run_refuses_an_expect_spec_when_the_judge_cli_is_missing` at `tests/test_run_cli.py:580`. KEEP. line 602: `assert result.exit_code == 2`; line 605: `assert '--judge-model pi' in result.output`.
- `test_run_ignores_a_missing_judge_cli_when_no_task_has_expect` at `tests/test_run_cli.py:608`. KEEP. line 645: `assert ran`.
- `test_run_rejects_a_value_below_one_before_running` at `tests/test_run_cli.py:652`. KEEP. line 666: `assert result.exit_code == 1`; line 667: `assert f'{flag} must be at least 1' in result.output`.
- `test_a_cheat_stays_flagged_in_live_progress_after_later_attempts` at `tests/test_run_cli.py:670`. KEEP. line 715: `assert result.exit_code == 0`; line 716: `assert updates == [True, True, True]`.
- `test_a_login_stop_off_a_terminal_names_the_command_and_waits_for_nothing` at `tests/test_run_cli.py:779`. KEEP. line 787: `assert result.exit_code == 2`; line 788: `assert 'Not logged in' in result.output`; line 789: `assert 'claude auth login' in result.output`; line 790: `assert 'Log in to' not in result.output`; line 791: `assert (logins, reruns) == ([], [])`.
- `test_yes_logs_in_then_reruns_the_same_command` at `tests/test_run_cli.py:794`. KEEP. line 807: `assert result.exit_code == 0`; line 808: `assert 'Log in to claude-code now with `claude auth login`?' in result.output`; line 809: `assert saved == [partial]`; line 810: `assert logins == [['claude', 'auth', 'login']]`; line 811: `assert reruns == [('/venv/bin/python', ['/venv/bin/python', *argv])]`.
- `test_no_exits_two_without_logging_in` at `tests/test_run_cli.py:816`. KEEP. line 822: `assert result.exit_code == 2`; line 823: `assert 'Log in to claude-code now' in result.output`; line 824: `assert (logins, reruns) == ([], [])`.
- `test_a_failed_login_exits_two_without_rerunning` at `tests/test_run_cli.py:827`. KEEP. line 833: `assert result.exit_code == 2`; line 834: `assert logins == [['claude', 'auth', 'login']]`; line 835: `assert '`claude auth login` did not complete the login.' in result.output`; line 836: `assert reruns == []`.
- `test_a_login_command_that_cannot_start_exits_two` at `tests/test_run_cli.py:839`. KEEP. line 850: `assert result.exit_code == 2`; line 851: `assert 'Could not run `claude auth login`' in result.output`; line 852: `assert reruns == []`.
- `test_a_stop_with_no_login_command_never_asks` at `tests/test_run_cli.py:863`. KEEP. line 869: `assert result.exit_code == 2`; line 870: `assert 'Log in to' not in result.output`; line 871: `assert (logins, reruns) == ([], [])`.
- `test_only_the_foreground_job_is_asked` at `tests/test_run_cli.py:883`. KEEP. line 893: `assert run_module._interactive() is asks`.
- `test_run_refuses_an_unusable_results_root_before_any_attempt` at `tests/test_run_cli.py:902`. KEEP. line 918: `assert result.exit_code == 2`; line 919: `assert 'Cannot save runs' in result.output`.
- `test_a_run_whose_save_fails_is_kept_elsewhere` at `tests/test_run_cli.py:922`. KEEP. line 938: `assert result.exit_code == 2`; line 939: `assert 'Run not saved' in result.output`; line 941: `assert len(kept) == 1`; line 942: `assert RunResults.model_validate_json(kept[0].read_text()).run.spec == 'sample'`.
- `test_reading_commands_diagnose_an_unusable_results_root` at `tests/test_run_cli.py:946`. KEEP. line 953: `assert result.exit_code == 2`; line 954: `assert 'Cannot save runs' in result.output`.

### test_engine.py

- `test_the_judge_follows_the_skill_backend_unless_named` at `tests/test_engine.py:30`. KEEP. line 33: `assert resolve_engine(model, judge_model) == expected`.
- `test_an_unknown_backend_names_the_flag_and_the_known_backends` at `tests/test_engine.py:40`. KEEP. line 43: `pytest.raises(CannotRun)`; line 47: `assert f'in {flag}' in message`; line 48: `assert 'Known backends: claude-code, codex, hermes, pi.' in message`.
- `test_a_missing_named_judge_points_back_to_the_model_backend` at `tests/test_engine.py:68`. KEEP. line 71: `pytest.raises(CannotRun)`; line 74: `assert str(raised.value) == "--judge-model pi asks pi to grade the `expect:` checks, but the pi CLI isn't installed.\n\nInstall and sign in to the pi CLI, or remove --judge-model and codex (your --model) will grade too."`.
- `test_a_missing_named_judge_on_the_model_backend_offers_another_backend` at `tests/test_engine.py:82`. KEEP. line 87: `pytest.raises(CannotRun)`; line 90: `assert str(raised.value) == "--judge-model pi asks pi to grade the `expect:` checks, but the pi CLI isn't installed.\n\nInstall and sign in to the pi CLI, or point --judge-model at an installed backend."`.
- `test_a_missing_default_judge_points_at_the_model_flag` at `tests/test_engine.py:98`. KEEP. line 99: `pytest.raises(CannotRun)`; line 102: `assert str(raised.value) == "The pi CLI isn't installed. It would run the agent (--model) and, with no --judge-model, grade the `expect:` checks too.\n\nInstall and sign in to the pi CLI, or pick an installed one with --model."`.
- `test_a_spec_without_expect_never_needs_the_judge_cli` at `tests/test_engine.py:110`. KEEP. line 111: `assert check_judge_cli(resolve_engine('codex', 'pi'), _spec(tmp_path, _ASSERT)) is None`.
- `test_an_installed_judge_cli_passes` at `tests/test_engine.py:116`. KEEP. line 121: `assert check_judge_cli(resolve_engine('codex', 'pi'), _spec(tmp_path, _EXPECT)) is None`.
- `test_the_engine_builds_the_backends_it_names` at `tests/test_engine.py:126`. KEEP. line 129: `assert (engine.harness().name, engine.harness().model) == ('codex', 'gpt-5-codex')`; line 130: `assert (engine.judge().backend, engine.judge().model) == ('pi', 'm')`.

### test_parse_target.py

- `test_parse_target` at `tests/test_parse_target.py:35`. KEEP. line 36: `assert parse_target(value) == expected`.

### test_not_checked.py

- `test_not_checked_is_neither_usable_nor_noise` at `tests/test_not_checked.py:34`. KEEP. line 35: `assert Outcome.NOT_CHECKED.is_usable is False`; line 36: `assert Outcome.NOT_CHECKED.is_execution_noise is False`; line 38: `assert Outcome.NOT_CHECKED.is_activation_usable is True`.
- `test_not_checked_does_not_inflate_the_unusable_count` at `tests/test_not_checked.py:41`. KEEP. line 43: `assert task.usable == 0`; line 44: `assert task.unusable == 0`; line 45: `assert task.score is None`.
- `test_judge_errors_are_still_counted_as_noise` at `tests/test_not_checked.py:48`. KEEP. line 49: `assert task_result(Outcome.JUDGE_ERROR, Outcome.PASS).unusable == 1`.
- `test_usable_is_derived_not_subtracted` at `tests/test_not_checked.py:52`. KEEP. line 69: `assert tr.usable == 1`; line 70: `assert tr.score == 1.0`.
- `test_runner_skips_the_paid_judge_for_an_activates_only_task` at `tests/test_not_checked.py:76`. KEEP. line 107: `assert judge.calls == 0`; line 108: `assert [a.outcome for a in tr.attempts] == [Outcome.NOT_CHECKED] * 2`; line 110: `assert tr.score is None`; line 111: `assert tr.unusable == 0`.
- `test_trigger_only_task_is_detected` at `tests/test_not_checked.py:136`. CONSOLIDATE C8. line 137: `assert _trigger_task().trigger_only is True`.
- `test_trigger_only_task_reads_as_a_skip_not_an_error` at `tests/test_not_checked.py:140`. KEEP. line 143: `assert cell.plain.split() == ['—', 'probe']`; line 144: `assert all((str(span.style) == 'dim' for span in cell.spans))`.
- `test_a_trigger_probes_tokens_are_not_reported_as_wasted_spend` at `tests/test_not_checked.py:147`. KEEP. line 165: `assert totals.unusable_attempts == 0`; line 166: `assert totals.unusable_tokens == 0`; line 167: `assert totals.total_tokens == 24010`.
- `test_trigger_only_survives_one_timeout_among_k` at `tests/test_not_checked.py:173`. KEEP. line 189: `assert tr.trigger_only is True`; line 190: `assert _success_cell(tr, k=2).plain.split() == ['—', 'probe']`.
- `test_a_task_with_a_real_verdict_is_not_trigger_only` at `tests/test_not_checked.py:193`. KEEP. line 206: `assert tr.trigger_only is False`.
- `test_a_timeout_records_activation_as_unobserved_not_as_empty` at `tests/test_not_checked.py:209`. KEEP. line 234: `assert attempt.outcome is Outcome.TIMEOUT`; line 235: `assert attempt.activated is None`; line 236: `assert attempt.activation_passed is None`.
- `test_a_healthy_trigger_probe_resets_the_fail_fast_streak` at `tests/test_not_checked.py:239`. STRENGTHEN 1. line 242: `assert Outcome.NOT_CHECKED.is_execution_noise is False`; line 243: `assert Outcome.JUDGE_ERROR.is_execution_noise is True`.
- `test_an_unmeasured_run_has_no_headline` at `tests/test_not_checked.py:266`. KEEP. line 267: `assert _trigger_only_run().aggregate.measured is False`.
- `test_list_renders_an_unmeasured_run_as_skipped_not_zero` at `tests/test_not_checked.py:270`. KEEP. line 287: `assert listed.exit_code == 0`; line 288: `assert '2026-07-03T10-00-00Z' in listed.stdout`; line 289: `assert '0.0%' not in listed.stdout`; line 290: `assert 'no execution checks' in listed.stdout`.
- `test_list_still_prints_a_measured_score` at `tests/test_not_checked.py:293`. KEEP. line 301: `assert _score_cell(results) == '0.0%'`.
- `test_an_interrupted_unmeasured_run_keeps_its_marker` at `tests/test_not_checked.py:304`. KEEP. line 315: `assert RULE_GLYPH in cell`; line 316: `assert UNUSABLE_GLYPH in cell`.

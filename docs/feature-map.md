# Feature map

Each feature Caliper offers, how a test reaches it without a real agent CLI,
and the test that goes red when it breaks. Start here when a change touches a
feature: run its row's tests first, then the full suite.

`tests/test_doc_sync.py` checks this map: every `caliper` command and every
backend has a row, and every test a row names still exists.

Most rows reach Caliper through `typer.testing.CliRunner` against `caliper.main.app`,
with the agent and judge replaced by `ScriptedHarness` and `ScriptedJudge` from
`tests/conftest.py`. Backend rows instead fake the agent CLI's process, replaying
output in the shape the real CLI prints.

## Commands

| Feature | How a test reaches it | What proves it works |
| --- | --- | --- |
| `caliper run` | `run sample.eval.yaml` from the spec's folder, on a scripted run | Exit 0, and the run is saved under the discovered `.caliper/` root: `tests/test_run_cli.py::test_a_run_is_findable_by_report_from_another_directory`. Exit 2 when no attempt was usable: `tests/test_run_cli.py::test_run_cli_exits_two_when_no_attempt_was_usable` |
| `caliper run --k`, `--workers`, `--timeout`, `--model`, `--judge-model` | `run` with each flag, the run function stubbed | Each value reaches the run, and `backend:model` splits: `tests/test_run_cli.py::test_run_cli_forwards_options_to_run`, `tests/test_run_cli.py::test_run_cli_resolves_backend_and_judge_model_targets` |
| `caliper run --ablate` | `run --ablate NAME`, repeated | The named skill or server is missing from the attempt, and naming every member leaves the bare agent: `tests/test_run_cli.py::test_run_cli_collects_repeated_ablate_flags`, `tests/test_ablation.py::test_ablate_installs_the_neighbourhood_minus_the_named_skill`, `tests/test_ablation.py::test_ablating_every_member_leaves_the_bare_agent` |
| `caliper run --fail-fast` | The runner with a streak of unusable attempts | The task stops at the threshold, counting attempts not invocations: `tests/test_runner.py::test_runner_fail_fast_stops_after_unusable_threshold`, `tests/test_retry.py::test_fail_fast_counts_attempts_not_invocations` |
| `caliper run --user-customizations` | `run` with the flag, the spec setting, or neither | The notice and the forwarded setting follow flag, then spec, then default: `tests/test_run_cli.py::test_run_cli_user_customizations_notice` |
| `caliper run --output` | `run --output new/dir/out.json` | The results JSON lands in a directory that didn't exist: `tests/test_run_cli.py::test_run_cli_writes_output_to_a_new_directory` |
| `caliper run`, stopped with Ctrl-C | `run` whose stubbed run comes back interrupted | Exit 130, the partial run saved and marked interrupted: `tests/test_cancel.py::test_run_cli_exits_130_and_saves_an_interrupted_run` |
| `caliper validate` | `validate` on a broken, then a valid spec | Exit 1 naming what's wrong, and the resolved settings on success: `tests/test_load_spec.py::test_validate_rejects_the_spec`, `tests/test_load_spec.py::test_validate_shows_an_explicit_user_customizations_setting`. It stays offline: `tests/test_git_sources.py::test_validate_stays_offline_with_an_uncached_git_source` |
| `caliper list` | `list demo` over saved runs | Each row marks an interrupted run, an unmeasured run, and what a run ablated: `tests/test_measure_and_mark.py::test_list_marks_an_interrupted_run`, `tests/test_not_checked.py::test_list_renders_an_unmeasured_run_as_skipped_not_zero`, `tests/test_compare_cli.py::test_list_names_which_run_was_the_control_arm` |
| `caliper report` | `report sample --format json` from another folder | The saved run is found from anywhere in the project, and not from outside it: `tests/test_run_cli.py::test_a_run_is_findable_by_report_from_another_directory`, `tests/test_run_cli.py::test_report_outside_the_project_does_not_find_its_runs`. Success and activation print apart: `tests/test_activation_report.py::test_report_prints_both_scoreboards_separately` |
| `caliper compare` | `compare A B`, as a table and as `--format json` | The JSON carries the numbers the table prints: `tests/test_compare_cli.py::test_json_carries_the_numbers_the_table_prints`. A regression exits 0: `tests/test_measure_and_mark.py::test_compare_reports_a_regression_without_failing`. One run twice exits 1: `tests/test_compare_cli.py::test_one_spec_name_twice_is_refused` |
| `caliper login chatgpt` | The sign-in flow against a fake auth server and clock | A first sign-in registers Caliper and stores the login, and a declined one stores nothing: `tests/test_chatgpt_login.py::test_a_first_sign_in_registers_caliper_and_stores_the_login`, `tests/test_chatgpt_login.py::test_a_declined_sign_in_stores_nothing` |
| `caliper logout chatgpt` | Sign-out against the same fake | The session is revoked and forgotten, even offline: `tests/test_chatgpt_login.py::test_signing_out_revokes_the_session_and_forgets_it`, `tests/test_chatgpt_login.py::test_signing_out_offline_still_forgets_the_login` |
| `caliper telemetry` | `telemetry disable`, `enable`, `status`, then any command, with delivery faked | The first command sends nothing, the preference sticks, and the payload carries no paths, models, errors or arguments: `tests/test_telemetry.py::test_first_run_shows_notice_and_sends_nothing_then_collects`, `tests/test_telemetry.py::test_enable_and_disable`, `tests/test_telemetry.py::test_whole_payload_excludes_paths_models_errors_and_arguments` |

## Spec features

| Feature | How a test reaches it | What proves it works |
| --- | --- | --- |
| `expect:` | A task with `expect:`, the judge CLI faked | Only the LLM judge grades it: `tests/test_judge.py::test_eval_judge_expect_only_calls_llm` |
| `assert:` | A task with an assert script | The script grades it with no LLM call, and with `expect:` both must pass: `tests/test_judge.py::test_eval_judge_assert_only_runs_script_no_llm`, `tests/test_judge.py::test_eval_judge_both_checks_must_pass` |
| `activates:` and trigger probes | An `activates:`-only task through the runner | No paid judge call, and the run exits 0: `tests/test_not_checked.py::test_runner_skips_the_paid_judge_for_an_activates_only_task`, `tests/test_run_cli.py::test_run_cli_exits_zero_for_a_trigger_probe_run` |
| `sandbox.forbidden_files` | A transcript whose tool call reads a forbidden path | The read is a violation, and the attempt stays `cheat` in the live view: `tests/test_sandbox.py::test_declared_pattern_is_a_violation`, `tests/test_run_cli.py::test_a_cheat_stays_flagged_in_live_progress_after_later_attempts` |
| `setup:` and `cleanup:` hooks | Hooks that fail, through the runner and the CLI | A failed setup can't pass and still cleans up, and a failed cleanup exits 2: `tests/test_runner.py::test_failed_setup_cannot_pass_from_stale_artifact_and_still_cleans_up`, `tests/test_run_cli.py::test_run_cli_exits_two_after_cleanup_failure` |
| `mcp:` servers | A stdio server script the test writes | The preflight initializes it, and `--ablate` removes it: `tests/test_mcp.py::test_preflight_initializes_a_local_server`, `tests/test_ablation.py::test_ablate_removes_a_declared_mcp_server` |
| Git skill sources | A local git repository as the source | A branch resolves to a commit, and a pinned SHA serves from cache offline: `tests/test_git_sources.py::test_fetch_resolves_a_branch_to_a_commit`, `tests/test_git_sources.py::test_a_pinned_sha_is_served_from_cache_without_network` |
| `history:` | A task with planted turns | Each attempt gets the turns, and the judge sees them as prior context: `tests/test_history.py::test_each_attempt_is_handed_its_tasks_planted_turns`, `tests/test_history.py::test_the_judge_sees_the_planted_turns_as_prior_context` |

## Backends

| Feature | How a test reaches it | What proves it works |
| --- | --- | --- |
| `claude-code` | A faked `claude -p` process | The declared servers are written for the attempt, an unavailable model stops the run, and the judge calls `claude`: `tests/test_claude_harness.py::test_claude_harness_materializes_mcp_config`, `tests/test_claude_harness.py::test_claude_harness_stops_the_run_on_an_unavailable_model`, `tests/test_judge.py::test_eval_judge_claude_code_invokes_claude_cli` |
| `codex` | A faked `codex exec --json` process | Tool calls are captured, the judge answers from its last-message file, and a lapsed login names its command: `tests/test_codex_harness.py::test_codex_json_stream_captures_tool_calls`, `tests/test_codex_harness.py::test_codex_judge_answers_from_its_last_message_file`, `tests/test_codex_harness.py::test_codex_with_a_lapsed_login_names_its_login_command` |
| `hermes` | A faked `hermes` process and its `hermes sessions export` record | Token usage is parsed end to end, and a missing CLI stops the run: `tests/test_hermes_harness.py::test_hermes_run_captures_token_usage_end_to_end`, `tests/test_hermes_harness.py::test_hermes_missing_cli_raises_configuration_error` |
| `pi` | A faked `pi --mode json` process | Token usage is parsed end to end, and an expired login stops the run and saves nothing: `tests/test_pi_harness.py::test_pi_run_captures_token_usage_end_to_end`, `tests/test_pi_harness.py::test_pi_expired_oauth_stops_the_run_and_saves_nothing` |
| `opencode` | A faked `opencode run --format json` process | Turns, answer and summed usage are read, and a planted history is imported then resumed: `tests/test_opencode_harness.py::test_opencode_reads_turns_answer_and_summed_usage`, `tests/test_opencode_harness.py::test_a_planted_history_is_imported_then_resumed` |
| `antigravity` | A faked `agy` process replaying a stream in `tests/fixtures/antigravity` | Tool calls, answer and usage are read, and no login asks to sign in: `tests/test_antigravity_harness.py::test_antigravity_reads_tool_calls_answer_and_usage`, `tests/test_antigravity_harness.py::test_antigravity_without_a_login_asks_to_sign_in` |

These rows prove Caliper reads each CLI the way it was captured. When a CLI
changes its output, only the `tests/*-smoke.eval.yaml` runs against the real
CLI catch it; they're manual, as `.github/CONTRIBUTING.md` explains.

## Skill drivers

| Feature | How a test reaches it | What proves it works |
| --- | --- | --- |
| `hillclimb-skill`'s `climb.py` | `climb.py split`, `failures` and `scores` on saved runs | The split holds a third out and never moves, and no command shows a held-out task: `tests/test_hillclimb_skill.py::test_split_holds_out_a_third_and_is_fixed_for_the_climb`, `tests/test_hillclimb_skill.py::test_failures_shows_train_tasks_only`, `tests/test_state_machines.py::TestHeldOutBoundary` |

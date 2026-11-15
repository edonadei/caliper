# Feature map

Each feature Caliper offers, how a test reaches it without a real agent CLI,
and the test that goes red when it breaks. Start here when a change touches a
feature: run its row's tests first, then the full suite.

`tests/test_doc_sync.py` checks this map: every `caliper` command, each of its
flags and every backend has a row, and every test a row names still exists.
It can't check that a test asserts what its row says, so keep the two in step
when you change either.

Command rows invoke `caliper.main.app` through `typer.testing.CliRunner`. Most
replace the run itself with a stub that returns a finished run, so they prove
the command's wiring, exit code and output, not the runner. The runner, the
judge and each backend have rows of their own below, which call them directly.

## Commands

| Feature | How a test reaches it | What proves it works |
| --- | --- | --- |
| `caliper run` | `caliper run sample.eval.yaml` from the spec's folder, the run stubbed | Exit 0, and the run is saved under the discovered `.caliper/` root: `tests/test_run_cli.py::test_a_run_is_findable_by_report_from_another_directory`. Exit 2 when no attempt was usable: `tests/test_run_cli.py::test_run_cli_exits_two_when_no_attempt_was_usable` |
| `caliper run --k`, `--workers`, `--timeout`, `--verbose` | `caliper run sample.eval.yaml --k 3 --workers 2 --fail-fast 2`, then `--timeout 30 --verbose` | Each value reaches the run, `--timeout` defaults to 120, and `--verbose` reaches the report: `tests/test_run_cli.py::test_run_cli_forwards_options_to_run`, `tests/test_run_cli.py::test_run_cli_passes_timeout_to_the_run_and_verbose_to_the_report` |
| `caliper run --model`, `--judge-model` | `caliper run sample.eval.yaml --model codex:gpt-5-codex@high --judge-model pi:claude-sonnet-4-6@low` | Each target splits into backend, model and effort, for the agent and the judge: `tests/test_run_cli.py::test_run_cli_resolves_backend_and_judge_model_targets` |
| `caliper run --model backend@effort` | An engine built from `backend:model@effort` | The effort reaches the agent's and the judge's command line, and a backend that takes no such effort refuses it: `tests/test_cli_harness.py::test_a_named_effort_reaches_the_agent_and_the_judge`, `tests/test_engine.py::test_an_effort_the_backend_does_not_take_is_refused` |
| `caliper run --ablate` | `caliper run sample.eval.yaml --ablate grilling --ablate docs`, then the environment it builds | The flags are collected, the named skill is missing from the attempt, and naming every member leaves the bare agent: `tests/test_run_cli.py::test_run_cli_collects_repeated_ablate_flags`, `tests/test_ablation.py::test_ablate_installs_the_neighbourhood_minus_the_named_skill`, `tests/test_ablation.py::test_ablating_every_member_leaves_the_bare_agent` |
| `caliper run --fail-fast` | The runner on a scripted streak of unusable attempts | The task stops at the threshold, counting attempts, not invocations: `tests/test_runner.py::test_runner_fail_fast_stops_after_unusable_threshold`, `tests/test_retry.py::test_fail_fast_counts_attempts_not_invocations` |
| `caliper run --user-customizations` | `caliper run sample.eval.yaml` with `--user-customizations`, `--no-user-customizations`, the spec's setting, or none | The notice and the forwarded setting follow the flag, then the spec, then the default: `tests/test_run_cli.py::test_run_cli_user_customizations_notice` |
| `caliper run --output` | `caliper run sample.eval.yaml --output some/new/dir/out.json` | The results JSON lands in a folder that didn't exist: `tests/test_run_cli.py::test_run_cli_writes_output_to_a_new_directory` |
| `caliper run`, stopped with Ctrl-C | `caliper run sample.eval.yaml`, the stub returning an interrupted run | Exit 130, and the partial run is saved: `tests/test_cancel.py::test_run_cli_exits_130_and_saves_an_interrupted_run`. That a stop really kills the agent is the state machine's job: `tests/test_state_machines.py::TestTrackedProcess` |
| `caliper validate` | `caliper validate broken.eval.yaml`, then a valid spec | Exit 1 naming what's wrong, and the resolved settings on success: `tests/test_load_spec.py::test_validate_rejects_the_spec`, `tests/test_load_spec.py::test_validate_shows_an_explicit_user_customizations_setting`. It stays offline: `tests/test_git_sources.py::test_validate_stays_offline_with_an_uncached_git_source` |
| `caliper list` | `caliper list demo` over saved runs | Rows mark an interrupted run, an unmeasured run, and what a run ablated: `tests/test_measure_and_mark.py::test_list_marks_an_interrupted_run`, `tests/test_not_checked.py::test_list_renders_an_unmeasured_run_as_skipped_not_zero`, `tests/test_compare_cli.py::test_list_names_which_run_was_the_control_arm` |
| `caliper report` | `caliper report sample`, from inside and from outside the project | The saved run is found from anywhere in the project, and not from outside it: `tests/test_run_cli.py::test_a_run_is_findable_by_report_from_another_directory`, `tests/test_run_cli.py::test_report_outside_the_project_does_not_find_its_runs` |
| `caliper report --run`, `--verbose` | `caliper report demo --run 2026-08-04T10-00-00Z --verbose`, then `caliper report demo -v` | The named run is read, not the latest, and a passing task's output shows only under `--verbose`: `tests/test_compare_cli.py::test_report_reads_the_run_it_is_named_and_verbose_shows_a_pass` |
| `caliper report --format` | `caliper report demo --format json` | The JSON is the saved run plus its usage totals: `tests/test_compare_cli.py::test_report_json_is_the_saved_run_plus_its_usage_totals`. An unknown format exits 2: `tests/test_compare_cli.py::test_an_unknown_format_is_refused` |
| `caliper compare` | `caliper compare A.json B.json` | A regression exits 0: `tests/test_measure_and_mark.py::test_compare_reports_a_regression_without_failing`. One run named twice exits 1: `tests/test_compare_cli.py::test_one_spec_name_twice_is_refused` |
| `caliper compare --format`, `--verbose` | `caliper compare A.json B.json --format json`, then with and without `--verbose` | The JSON carries the per-attempt usage and activation deltas the table prints, and `--verbose` adds pass@k and pass^k: `tests/test_compare_cli.py::test_json_carries_the_numbers_the_table_prints`, `tests/test_compare_cli.py::test_compare_verbose_adds_pass_at_k` |
| `caliper login` | `caliper login chatgpt` against a fake auth server, the browser following its redirect | Exit 0 naming the account, and a declined sign-in exits 2: `tests/test_chatgpt_login.py::test_login_and_logout_through_the_cli`, `tests/test_chatgpt_login.py::test_a_declined_login_exits_two`. The token flow itself: `tests/test_chatgpt_login.py::test_a_first_sign_in_registers_caliper_and_stores_the_login` |
| `caliper logout` | `caliper logout chatgpt` after that sign-in, then again | The login is forgotten, and a second logout says it's not signed in: `tests/test_chatgpt_login.py::test_login_and_logout_through_the_cli`. The session is revoked, even offline: `tests/test_chatgpt_login.py::test_signing_out_revokes_the_session_and_forgets_it`, `tests/test_chatgpt_login.py::test_signing_out_offline_still_forgets_the_login` |
| `caliper telemetry status`, `caliper telemetry enable`, `caliper telemetry disable` | `caliper telemetry status`, then `enable` and `disable` around `caliper validate`, with delivery faked | Status names no saved preference at first and nothing is sent, the preference sticks, and the payload carries no paths, models, errors or arguments: `tests/test_telemetry.py::test_first_run_shows_notice_and_sends_nothing_then_collects`, `tests/test_telemetry.py::test_enable_and_disable`, `tests/test_telemetry.py::test_whole_payload_excludes_paths_models_errors_and_arguments` |

## Spec features

| Feature | How a test reaches it | What proves it works |
| --- | --- | --- |
| `skills:` from paths | A spec listing local `SKILL.md` folders | Each installs under its frontmatter name, with its whole folder: `tests/test_skills.py::test_installs_at_skills_root_under_the_frontmatter_name`, `tests/test_skills.py::test_install_carries_the_whole_directory_for_progressive_disclosure` |
| `skills:` from git | A local git repository as the source | A branch resolves to a commit, and a pinned SHA is served from cache offline: `tests/test_git_sources.py::test_fetch_resolves_a_branch_to_a_commit`, `tests/test_git_sources.py::test_a_pinned_sha_is_served_from_cache_without_network` |
| `expect:` | The judge on a task with `expect:`, its CLI faked | Only the LLM judge grades it: `tests/test_judge.py::test_eval_judge_expect_only_calls_llm` |
| `assert:` | The judge on a task with an assert script | The script grades it with no LLM call, and with `expect:` both must pass: `tests/test_judge.py::test_eval_judge_assert_only_runs_script_no_llm`, `tests/test_judge.py::test_eval_judge_both_checks_must_pass` |
| `activates:` | A trigger probe through the runner, and `caliper run` on one | No paid judge call, and the run exits 0: `tests/test_not_checked.py::test_runner_skips_the_paid_judge_for_an_activates_only_task`, `tests/test_run_cli.py::test_run_cli_exits_zero_for_a_trigger_probe_run` |
| `sandbox.forbidden_files` | An attempt whose transcript reads a forbidden path | The read is a violation, and the attempt ends as `cheat` without a judge call: `tests/test_sandbox.py::test_declared_pattern_is_a_violation`, `tests/test_attempt.py::test_a_forbidden_file_read_is_a_cheat_and_skips_the_judge` |
| `sandbox.extra_path` | Each backend's environment with an extra path | The staged binaries come first on `PATH`, once: `tests/test_cli_harness.py::test_extra_path_wins_over_the_real_path` |
| `setup:` and `cleanup:` | The runner with hooks that really exit nonzero | A failed setup can't pass on a stale artifact and still cleans up, and a failed cleanup keeps the agent's outcome: `tests/test_runner.py::test_failed_setup_cannot_pass_from_stale_artifact_and_still_cleans_up`, `tests/test_runner.py::test_failed_cleanup_keeps_agent_outcome_and_reports_both_hook_failures`. `caliper run` exits 2 on a recorded hook failure: `tests/test_run_cli.py::test_run_cli_exits_two_after_cleanup_failure` |
| `mcp:` stdio servers | A stdio server script the test writes | The preflight initializes it, and `--ablate` removes it: `tests/test_mcp.py::test_preflight_initializes_a_local_server`, `tests/test_ablation.py::test_ablate_removes_a_declared_mcp_server` |
| `mcp:` remote servers | A spec declaring a `url` and `headers` | The URL and headers are interpolated, and `claude-code` writes them for the attempt: `tests/test_mcp.py::test_resolve_servers_interpolates_remote_url_and_headers`, `tests/test_claude_harness.py::test_claude_harness_materializes_remote_mcp_config` |
| `history:` | A task with planted turns | Each attempt gets the turns, and the judge sees them as prior context: `tests/test_history.py::test_each_attempt_is_handed_its_tasks_planted_turns`, `tests/test_history.py::test_the_judge_sees_the_planted_turns_as_prior_context` |

## Backends

Each backend's harness is called directly, its CLI process faked with output in
the shape the real CLI prints.

| Feature | How a test reaches it | What proves it works |
| --- | --- | --- |
| `claude-code` | A faked `claude -p` process | The declared servers are written for the attempt, an unavailable model stops the run, and the judge calls `claude -p`: `tests/test_claude_harness.py::test_claude_harness_materializes_mcp_config`, `tests/test_claude_harness.py::test_claude_harness_stops_the_run_on_an_unavailable_model`, `tests/test_judge.py::test_eval_judge_claude_code_invokes_claude_cli` |
| `codex` | A faked `codex exec --json` process | Tool calls are captured, the judge answers from its last-message file, and a lapsed login names its command: `tests/test_codex_harness.py::test_codex_json_stream_captures_tool_calls`, `tests/test_codex_harness.py::test_codex_judge_answers_from_its_last_message_file`, `tests/test_codex_harness.py::test_codex_with_a_lapsed_login_names_its_login_command` |
| `hermes` | A faked `hermes` process and its `hermes sessions export` record | Token usage is read from the export, and a CLI not on `PATH` is a configuration error: `tests/test_hermes_harness.py::test_hermes_run_captures_token_usage_end_to_end`, `tests/test_hermes_harness.py::test_hermes_missing_cli_raises_configuration_error` |
| `pi` | A faked `pi --mode json` process | Token usage is read end to end, and an expired login stops the run and saves nothing: `tests/test_pi_harness.py::test_pi_run_captures_token_usage_end_to_end`, `tests/test_pi_harness.py::test_pi_expired_oauth_stops_the_run_and_saves_nothing` |
| `opencode` | A faked `opencode run --format json` process | Turns, answer and summed usage are read, and a planted history is imported, then resumed: `tests/test_opencode_harness.py::test_opencode_reads_turns_answer_and_summed_usage`, `tests/test_opencode_harness.py::test_a_planted_history_is_imported_then_resumed` |
| `antigravity` | A faked `agy` process replaying a stream in `tests/fixtures/antigravity` | Tool calls, answer and usage are read, and no login asks to sign in: `tests/test_antigravity_harness.py::test_antigravity_reads_tool_calls_answer_and_usage`, `tests/test_antigravity_harness.py::test_antigravity_without_a_login_asks_to_sign_in` |

These rows prove Caliper reads each CLI's output in the shape it was captured.
When a CLI changes that shape, only the `tests/*-smoke.eval.yaml` runs against
the real CLI catch it. They're manual, as `.github/CONTRIBUTING.md` explains.

## Skill drivers

| Feature | How a test reaches it | What proves it works |
| --- | --- | --- |
| `hillclimb-skill`'s `climb.py` | `climb.py split`, `failures` and `scores` on saved runs | The split holds a third out and never moves, and no command shows a held-out task: `tests/test_hillclimb_skill.py::test_split_holds_out_a_third_and_is_fixed_for_the_climb`, `tests/test_hillclimb_skill.py::test_failures_shows_train_tasks_only`, `tests/test_state_machines.py::TestHeldOutBoundary` |

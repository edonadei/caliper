# Test suite audit, 10 October 2026

Seven tests can be cut conservatively. No whole module earns deletion. Most similar-looking tests protect different inputs or different boundaries. The larger opportunity is to repair misleading tests and consolidate repeated fixtures without dropping assertions.

This matters to maintainers through less duplicate review work and more reliable regression protection. End-user behavior stays unchanged under the proposed cuts. No test or production file was changed during this audit.

## Scope and sync

Audited main at `e8a9302fa355653f95ff7c6d46b4d94ece9cae3c`. The suite has 47 pytest modules, 914 test functions and 1,200 collected cases after parametrization. Test modules contain 18,762 source lines. Every module was reviewed, including assertions, helpers, relevant production code and regression history where needed. Three explorers covered separate slices; the parent covered spec loading and skills, verified candidates and owns this report.

At audit time, the checkout tracked origin/main at that SHA. The prior checkout remains on `codex/smoke-fixture-isolation`, including unpushed commit `9ddca1d`. Divergent local main was renamed to `codex/main-before-sync-20261010`, preserving its commit `7f8c234`. Existing untracked QA files were retained. Nothing was pushed or committed during the investigation. This PR packages that report and its evidence. Source links stay pinned to the audited SHA because main has advanced since then.

The [inventory script](test-suite-audit-2026-10-10/inventory.py) derives function counts from AST and case counts from executed JUnit XML. Its [inventory](test-suite-audit-2026-10-10/inventory.json) distinguishes functions from parametrized cases. Substantive reviews and assertion ledgers are in the [harness audit](test-suite-audit-2026-10-10/harness.md), [orchestration audit](test-suite-audit-2026-10-10/orchestration.md), [results audit](test-suite-audit-2026-10-10/results.md), and [spec/skill audit](test-suite-audit-2026-10-10/spec-skills.md).

## Seven conservative cuts

These are source-inspected recommendations. Five concrete mutations also establish that selected surviving tests still catch the defect they protect. They are sampled proofs, not exhaustive mutation coverage.

| Remove | Retain | Why the removed test adds little |
| --- | --- | --- |
| [test_eval_judge_always_returns_eval_judge_instance](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_judge.py#L82) | Actual judge calls for [Claude alias](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_judge.py#L300), [claude-code](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_judge.py#L347), [Codex](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_judge.py#L381), [Hermes](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_judge.py#L583), and [Pi](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_judge.py#L660) | Direct construction followed only by isinstance. The constructor no longer dispatches to different judge classes. Surviving tests construct it and observe verdicts and transport payloads. |
| [test_a_cap_on_a_zero_exit_still_stops_the_run](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_retry.py#L389) | [test_a_spending_cap_raises_instead_of_retrying](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_retry.py#L147) | Both reach the identical zero-exit CAPPED input and policy. The survivor checks the same exception, one invocation and its diagnostic. Its queued second result is never reached. |
| [test_a_passing_attempt_that_mentions_a_throttle_is_not_respawned](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_retry.py#L380) | [test_a_passing_attempt_that_mentions_a_cap_does_not_abort_the_run](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_retry.py#L370) | Despite different names, both use the identical ANSWER_ABOUT_LIMITS result. The survivor checks one call, zero retries and exact retained output. An erroneous retry also fails its one-item queue. |
| [test_a_task_with_a_real_verdict_is_not_trigger_only](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_task_metrics.py#L123) | [The same predicate test in test_not_checked](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_not_checked.py#L193) | Both use ordered PASS/NOT_CHECKED outcomes, activation_expected=None and the same getter, asserting literal False. |
| [test_the_interrupt_code_is_named](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_diagnosis.py#L171) | [test_run_cli_exits_130_and_saves_an_interrupted_run](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_cancel.py#L680) | The survivor invokes the real command branch, consumes the enum and checks literal exit 130 plus persistence. The removed test only pins the enum integer. |
| [test_codex_supports_mcp](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_codex_harness.py#L562) | Actual Codex runs preserving [user servers/connectors](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_codex_harness.py#L635) and [spec precedence](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_codex_harness.py#L710) | The class-field pin is already consumed by real staging behavior. Disabling the capability makes both surviving customization inventory assertions fail. |
| [test_a_file_too_large_to_install_is_not_captured](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_skillsnapshot.py#L273) | [test_the_snapshot_holds_exactly_the_files_the_install_wrote](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_skillsnapshot.py#L282) | Both include the same six-MiB blob and reach the same size-filter path. The invariant additionally supplies answers/ filtering and a larger fixture, so full inputs differ. The current size rule is independent of that filtering. The invariant checks exact installed keys and their equality with snapshot keys, rejecting the blob on both sides. |

Removing these seven functions would leave 907 functions and 1,193 cases. A selective run of the affected modules and the interrupted-command survivor, with exactly those seven cases deselected, produced [168 passes and seven deselections](test-suite-audit-2026-10-10/pruning-probe-seven.txt). This verifies the selected retained tests execute successfully. The cross-module trigger survivor passed in the baseline and failed its targeted mutant. This selection does not by itself prove coverage equivalence.

The survivor mutation checks failed as expected when [spending caps were ignored](test-suite-audit-2026-10-10/mutant-cap.txt), [trigger_only always returned True](test-suite-audit-2026-10-10/mutant-trigger.txt), [the interrupted exit became 129](test-suite-audit-2026-10-10/mutant-interrupt-code.txt), [Codex MCP support became False](test-suite-audit-2026-10-10/mutant-codex-mcp.txt), and [oversized files became installable](test-suite-audit-2026-10-10/mutant-oversized.txt).

## Repairs matter more than the cuts

### The update test can modify the host

[tests/test_update_cli.py:90](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_update_cli.py#L90) patches CODEX_APP_CLI but leaves CODEX_CLI_PATH and subprocess.run uncontrolled. An existing override wins before the fake bundle. The update command also suppresses bundle protection when that override is set.

The initial baseline actually executed `npm install -g @openai/codex`. Its [output reports two packages changed](test-suite-audit-2026-10-10/baseline.txt). The installed global package was then observed as 0.162.1. The prior version was not recorded, so no rollback is claimed. The user was informed during the audit.

Keep and repair this test. Clear or set resolver inputs in a fixture, and install a fail-closed subprocess fake before invoking `--yes`. A broken bundle guard must fail the test before an external update can run. Apply the same host-override isolation to Codex harness tests. Clearing the override in the audit process resolved all six related baseline failures.

### Fail-fast reset has a proven whole-suite blind spot

[test_a_healthy_trigger_probe_resets_the_fail_fast_streak](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_not_checked.py#L239) only checks two enum predicates. It never runs the scheduler.

A process-local mutation of [the reset branch](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/caliper/runner.py#L397) kept NOT_CHECKED neutral instead of resetting the streak. The entire guarded suite, with repository-eval scanning restricted to tracked files, still produced [1,198 passes and two platform skips](test-suite-audit-2026-10-10/mutant-fail-fast.txt).

A separate [direct runner probe](test-suite-audit-2026-10-10/fail_fast_behavior.py) verifies the mutation changes behavior. With k=4, one worker and fail_fast_unusable=2, the sequence TIMEOUT, NOT_CHECKED, TIMEOUT, NOT_CHECKED yields four records in current main. The mutant stops after three. [Observed outcomes](test-suite-audit-2026-10-10/fail-fast-behavior.txt) prove the defective branch was active.

Replace the predicate-only regression test with this exact scheduler scenario. Production behavior is correct in main. The weakness is that the suite permits this specific regression. This does not establish general scheduler mutation coverage.

### Thirteen MCP acceptance tests permit a no-op

The [harness audit lists all 13 acceptance tests](test-suite-audit-2026-10-10/harness.md). They cover initialization, protocol versions, notifications, a colliding ping, resource-only servers, extra PATH, host-secret exclusion, modern discovery, float TTL, valid tool lists and legacy null-error responses.

Replacing preflight with a no-op made [all 13 pass](test-suite-audit-2026-10-10/mcp-noop.txt). The [negative control failed](test-suite-audit-2026-10-10/mcp-noop-negative.txt), so the MCP suite as a whole is not blind to a global no-op.

Keep these scenarios and observe an actual exchange. Have each server write the received requests and relevant responses to a temporary marker file, then assert their literal contents. The ping case should record the pong. PATH and environment tests should prove startup and the relevant environment fact. Several acceptance tests are also Windows CI gates.

### Other fixtures and assertions overstate what they protect

These findings are source-inspected, except the normal temporary paths and repository-scan failure observed in the baseline.

| Test | Current blind spot | Repair |
| --- | --- | --- |
| [Relative Git source](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_git_sources.py#L422) | The ordinary tmp_path is outside cwd, so the test takes an absolute-path fallback. | Change cwd to tmp_path, pass './origin', and assert SHA and skill bytes. |
| [Judge timing](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_judge.py#L813) | Nonnegative/non-None accepts hardcoded zero. | Supply a deterministic clock and assert literal elapsed model time. Retain separate script-time exclusion. |
| [Cap abort saves completed work](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_retry.py#L280) | The fixture caps immediately and has no completed attempt to preserve. | Run one clean attempt followed by a cap and assert the saved first record. |
| [Unobserved activation dash](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_activation_report.py#L129) | expected=None takes the unasserted branch before examining missing observations. | Set expected=['mine'], activated=None and activation_passed=None. |
| [Unreported token dash](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_usage.py#L460) | It only checks '3s' anywhere in the output. | Assert the named task row's token dash and wall time. |
| [Compare secondary columns](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_compare.py#L284) | It compares two calls to the same scoring formulas without rendering columns. | Check literal rates, copied outcomes and actual verbose row values. |
| [Compare identical runs](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_compare.py#L71) | all(...) can succeed on an empty matched list. | Assert the exact nonempty matched task names and literal deltas. |
| [Runstore round trip](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_runstore.py#L29) | Only loaded.run.spec is checked. | Preserve distinctive metadata, attempts and transcript fields. |
| [Shared home seeding](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_cli_harness.py#L69) | Inputs and expected paths both come from seed_files. Omitted declarations disappear from the scenario. | Use literal backend inventories. Keep Pi's models.json regression. |

Reporter/compare/usage helpers also still construct singular skill_snapshot and outdated aggregates. Current schemas can ignore the legacy snapshot key, so some fixtures contain less actual metadata than they appear to. Repair the helpers and keep the meaningful output assertions.

## Consolidate repeated runs without deleting contracts

These are conditional recommendations, not immediate cuts. Copy every distinct assertion into the survivor first. Preserve separate inputs as parameter rows where required. Detailed reports contain exact function names, line numbers and reached-input comparisons.

| Group | Candidate and survivor | Required preservation |
| --- | --- | --- |
| Attempt timeout evidence | test_attempt.py:406 into :389 | Raw activation evidence, TIMEOUT, no admissible activation verdict or score. |
| Failed setup metadata | test_attempt.py:534 into :521 | Outcome/reason plus absent model, user customization and judge metadata. |
| Parallel barrier run | test_attempt_parallelism.py:115 into :94 | Ordered records. Force an observed out-of-order completion if preserving the stronger name. |
| Four ablation groups | test_ablation.py marker/snapshot/expectation pairs | Removed/kept skills and servers, RunMeta markers, snapshots and activation expectations. See orchestration C4 through C7. |
| Trigger-only predicate and render | test_not_checked.py:136 into :140 | Literal bool plus the rendered probe cell and dim styling. |
| Hermes command options | test_hermes_harness.py:373 into :46; :136 into :458 | --yolo, no --skills, no CALIPER_SKILL and model omission. Keep the skill-present branch. |
| Claude absent init | test_claude_harness.py:473 into :480 | Both unknown exposed skills and unknown MCP inventory. |
| Claude empty MCP | test_claude_harness.py:309 with :342 | Keep both None and {} inputs, strict config, exact empty contents and cleanup. |
| Legacy null-error MCP | test_mcp.py:760 with :1332 | Empty and nonempty tool-list cases plus real exchange evidence. |
| Activation denominators | test_activation_scoring.py:100 into :69; :279 into :212 | Literal execution/activation denominators and opportunities. |
| Drift metadata | test_skill_drift.py:86 into :75; :108 into :96 | Warnings, source kinds and exact old/new short refs or hashes. |
| Legacy optional fields | test_transcript_persistence.py:17 with test_usage.py:258 | Transcript=None, usage=None and passed=True for the same old JSON. |

Do not collapse expected=None and expected=[] trigger-only inputs. Do not treat markup-enabled and markup-disabled rendering as identical. Singleton and mixed-task visibility cases also remain separate unless a matrix retains both. These distinctions prevent an optimistic consolidation count from becoming lost coverage.

## Why most tests stay

Schema and scoring tests exercise public derived values and persistence rules. Attempt tests own verdict precedence and admissibility. Runner tests own setup, cleanup, retries, scheduling, metadata and callbacks. CLI tests own parsing, engine wiring, exit codes and persistence. The shared subprocess fake checks actual transport arguments, stdin, cwd and configuration. Similar assertions at these boundaries can fail for different defects.

Real subprocess tests add pipe draining, stdin backpressure, background writers, cancellation, orphaned tools, process groups and identity tracking. Keep them. The slow tests deliberately exercise time budgets and process lifecycle; their useful signal is not replaceable with a mock-call count.

Keep negative controls and legacy read-side contracts. Empty or None outputs can mean correct silence, unknown provenance, an unasserted check or no comparable score. They are not automatically weak assertions. Keep the reserved BAR_NOT_MET=3 declaration, since it is a published reservation with no emitted runtime path. Keep Pi's unsupported-MCP guard until a real Pi run-seam test replaces its declaration pin.

All seven manual backend/MCP smoke evals, with ten tasks, were inspected. Keep them. Paid smokes observe actual CLI compatibility that mocked tests cannot. No harness was changed, so paid smoke runs were not performed for this investigation.

## Every pytest module

Counts come from the executed baseline and AST inventory. Disposition is the audit judgment, not a change already applied.

| Module | Functions | Cases | Disposition |
| --- | ---: | ---: | --- |
| [test_ablation.py](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_ablation.py) | 38 | 38 | Keep; consolidate exact marker/snapshot pairs. |
| [test_activation.py](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_activation.py) | 21 | 28 | Keep literal detections and negative boundaries. |
| [test_activation_report.py](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_activation_report.py) | 21 | 21 | Keep; repair unobserved fixture. |
| [test_activation_scoring.py](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_activation_scoring.py) | 21 | 21 | Keep; consolidate denominators with all assertions. |
| [test_attempt.py](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_attempt.py) | 41 | 45 | Keep precedence; consolidate identical-input metadata pairs. |
| [test_attempt_parallelism.py](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_attempt_parallelism.py) | 6 | 7 | Keep real barriers; consolidate repeated run and prove finish order. |
| [test_backends.py](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_backends.py) | 5 | 9 | Keep registry/factory/protocol relations. |
| [test_cancel.py](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_cancel.py) | 18 | 22 | Keep real lifecycle, pipe and interrupted-persistence coverage. |
| [test_claude_harness.py](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_claude_harness.py) | 26 | 45 | Keep adapter contracts; consolidate absent-init/empty-MCP pairs. |
| [test_cli_harness.py](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_cli_harness.py) | 30 | 83 | Keep conformance; use literal seed inventories. |
| [test_codex_harness.py](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_codex_harness.py) | 31 | 36 | Cut one capability pin; repair host-override isolation. |
| [test_compare.py](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_compare.py) | 16 | 16 | Keep; replace formula/heading-only oracles with literal output. |
| [test_compare_cli.py](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_compare_cli.py) | 7 | 8 | Keep reference resolution and real command errors. |
| [test_diagnosis.py](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_diagnosis.py) | 19 | 20 | Cut interrupted integer pin; keep reserved code and error mappings. |
| [test_doc_sync.py](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_doc_sync.py) | 4 | 4 | Keep live declaration/documentation relations. |
| [test_engine.py](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_engine.py) | 8 | 14 | Keep resolution and availability diagnoses. |
| [test_environment.py](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_environment.py) | 11 | 14 | Keep direct environment and choice-provenance contracts. |
| [test_git_sources.py](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_git_sources.py) | 37 | 42 | Keep real local Git; repair relative-path premise. |
| [test_hermes_harness.py](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_hermes_harness.py) | 24 | 28 | Keep protocol/recovery; fold command assertions into shared runs. |
| [test_judge.py](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_judge.py) | 44 | 63 | Cut constructor tautology; keep combination/transport; strengthen timing. |
| [test_load_spec.py](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_load_spec.py) | 30 | 57 | Keep validation; tighten diagnostics and repository scan scope. |
| [test_mcp.py](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_mcp.py) | 66 | 83 | Keep protocol/lifecycle; strengthen 13 acceptance observations. |
| [test_measure_and_mark.py](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_measure_and_mark.py) | 12 | 12 | Keep recording/roll-up; assert regression output. |
| [test_not_checked.py](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_not_checked.py) | 16 | 16 | Keep probe semantics; replace fail-fast predicate-only regression. |
| [test_parse_target.py](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_parse_target.py) | 1 | 12 | Keep all 12 literal parser cases. |
| [test_pi_harness.py](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_pi_harness.py) | 13 | 14 | Keep zero-exit OAuth and models.json regressions; improve MCP guard. |
| [test_prompt_failure.py](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_prompt_failure.py) | 11 | 12 | Keep typed provider failure boundaries. |
| [test_refusal.py](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_refusal.py) | 9 | 9 | Keep precedence and answer-versus-refusal negative controls. |
| [test_reporter.py](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_reporter.py) | 39 | 39 | Keep display regressions; strengthen truncation/fixture evidence. |
| [test_retry.py](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_retry.py) | 20 | 20 | Cut two identical-input duplicates; retain loop and cost contracts. |
| [test_run_cli.py](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_run_cli.py) | 31 | 41 | Keep flags, forwarding, persistence, progress and exit behavior. |
| [test_run_seam.py](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_run_seam.py) | 5 | 5 | Keep ownership/model checks; strengthen context-isolation premise. |
| [test_runner.py](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_runner.py) | 26 | 29 | Keep real lifecycle, fail-fast and metadata assembly. |
| [test_runstore.py](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_runstore.py) | 30 | 38 | Keep filesystem/discovery; strengthen round-trip payload. |
| [test_sandbox.py](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_sandbox.py) | 17 | 28 | Keep false-positive controls; assert exact extensionless hits. |
| [test_scoring.py](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_scoring.py) | 12 | 12 | Keep usable/noise arithmetic and distinct denominators. |
| [test_skill_drift.py](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_skill_drift.py) | 9 | 9 | Keep provenance/warnings; consolidate identical fixtures. |
| [test_skills.py](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_skills.py) | 33 | 44 | Keep resolution/install; optional size-case consolidation. |
| [test_skillsnapshot.py](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_skillsnapshot.py) | 14 | 14 | Cut standalone oversized-file case; keep exact invariant. |
| [test_smoke_fixtures.py](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_smoke_fixtures.py) | 4 | 7 | Keep fresh-artifact and real transport lifecycle. |
| [test_task_metrics.py](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_task_metrics.py) | 21 | 21 | Cut exact trigger-only duplicate; keep computed/persisted facts. |
| [test_transcript_persistence.py](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_transcript_persistence.py) | 3 | 3 | Keep actual round trips; fold duplicate old JSON defaults. |
| [test_update_cli.py](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_update_cli.py) | 5 | 5 | Keep behavior; make external update boundary fail closed. |
| [test_usage.py](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_usage.py) | 26 | 29 | Keep normalization/unknown data; assert actual task rows. |
| [test_user_customizations.py](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_user_customizations.py) | 12 | 34 | Keep precedence, metadata, warnings and report contracts. |
| [test_user_layer.py](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_user_layer.py) | 10 | 32 | Keep copies, plugin staging, shadowing and builtin exclusions. |
| [test_workdir.py](https://github.com/edonadei/caliper/blob/e8a9302fa355653f95ff7c6d46b4d94ece9cae3c/tests/test_workdir.py) | 11 | 11 | Keep real scripts, draining, timeout and cleanup. |
| Total | 914 | 1,200 | Seven immediate candidates; no module deletion. |

## Verification and limits

| Run or check | Observed result |
| --- | --- |
| Original full suite | 1,191 passed, seven failed, two skipped. Six CLI override failures and one untracked invalid QA eval scanned by a repository guard. |
| Controlled full run | 1,197 passed, two skipped, one repository-scan test deselected. CLI overrides cleared and direct agent/npm executable basenames blocked. This is a run-specific guard, not a general shell or network sandbox. |
| Deselected repository test | Passed separately with Path.rglob restricted to git-tracked evals. Untracked fixtures were not moved or edited. |
| Seven-cut probe | 168 passed, seven deselected across affected modules and the interrupted-command survivor. |
| Five survivor mutations | Retained tests failed for all five chosen defects. |
| MCP no-op probe | All 13 acceptance tests passed; the negative control failed. |
| Fail-fast reset mutation | Full guarded suite passed with the defect active. Direct behavior probe confirmed four records became three. |
| Ruff 0.15.20 format . | 96 files unchanged. No tracked diff. |
| Ruff check . | One existing import-order violation in untracked docs/qa/cli-2026-09-24/probe.py. |
| Ruff over tracked Python files | All checks passed. |

The [guarded runner](test-suite-audit-2026-10-10/guarded_pytest.py), [mutation runner](test-suite-audit-2026-10-10/probes.py), [baseline log](test-suite-audit-2026-10-10/baseline.txt), [controlled log](test-suite-audit-2026-10-10/controlled.txt), and [tracked-scan result](test-suite-audit-2026-10-10/tracked-specs.txt) make these observations reviewable. The instrumentation and mutants are archived beside this report, outside production and tests. Mutations are applied only inside their process.

The guard checks direct command basenames. It does not block arbitrary wrappers or all shell-mediated execution. No paid CLI calls are observed in the controlled artifacts. Run local verification from the repository root with `.venv/bin/python docs/qa/test-suite-audit-2026-10-10/guarded_pytest.py --audit-tracked-specs -q`. That command deliberately controls host override variables and substitutes tracked-file enumeration for one repository scan. It is not an unchanged ordinary pytest baseline.

All execution was macOS Python 3.12.11. The two skipped tests are Windows Node startup and Windows background-shell reader cleanup. Linux/Python 3.10 through 3.13 and Windows CI were inspected, not executed here. No paid backend smoke was run. No coverage percentage or stable speedup is claimed. The cuts primarily reduce maintenance clutter; the meaningful slower process tests should remain.

## Principles and next action

Test Behavior, Not Implementation changed the selection rule to observable contracts, while preserving meaningful negative controls. Prove It Works led to the survivor mutations and the direct fail-fast behavior probe. Build the Lever led to a rerunnable inventory and guarded verification scripts. Model the Domain organized mutation cases by explicit names rather than scattered mode flags. These leaf skills were read for this session.

Repair update isolation first. Replace the fail-fast regression and strengthen MCP acceptance observations next. Then remove the seven conservative candidates, followed by bounded assertion-preserving consolidation. There is no evidence for a broad test purge.

## Attention

Reviewed by gpt-6.1-sol. The reviewer independently verified all 47 module assignments, inventory/XML counts, the seven cuts and their surviving contracts. It found no conflicting survivor removals. Its [review](test-suite-audit-2026-10-10/review.md) records the evidence limits. The baseline's real npm install and the specific whole-suite fail-fast mutation escape are the main findings requiring attention. The decision trail records a correction that its first four timestamps were placeholders, not observed action times. Evidence claims rely on artifacts and execution outputs, not those timestamps. The initial gpt-6-astra synthesis attempt failed before producing work because of a usage limit; a fresh gpt-6.1-sol reviewer completed the final report check. No cross-provider review is claimed.

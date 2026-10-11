# Results, reporting, comparison and persistence audit

ISSUES. Audited SHA `e8a9302fa355653f95ff7c6d46b4d94ece9cae3c`.

Evidence is inspected source and Git history. This explorer executed no tests, full-suite runs, timing measurements, coverage runs or mutation runs. Parent-run evidence is identified separately below. HEAD was checked against the requested SHA. The investigation writes only this report. Existing untracked documentation was left alone.

The slice contains 245 AST test functions, 265 cases after expansion of literal pytest parametrization, 576 assert statements, and eight direct pytest.raises checks inside test bodies. These are static counts, not collection or execution counts. Assertion checks in fixture helpers are separate. The larger case counts come from activation path boundaries, unknown CLI output formats, diagnostic rendering titles, runstore invalid names/numbers, and token field validation.

The strongest immediate deletion candidates are two small tests. Most apparent weaknesses in this slice deserve a better oracle or consolidation that preserves their distinct inputs. The expensive behaviors here are ordinary Rich rendering and temporary filesystem/CLI entry-point calls. There are no paid backend calls or intentional long sleeps in the assigned tests. Runtime savings from the two immediate candidates are therefore expected to be small, and are not measured.

## Per-module disposition

All test pointers below are under `tests/`.

| Module | AST functions | Expanded cases | Assert statements | Disposition |
| --- | ---: | ---: | ---: | --- |
| test_activation.py | 21 | 28 | 21 | Keep. Positive detections and exact negative boundaries are different contracts. |
| test_activation_report.py | 21 | 21 | 48 | Keep most. Strengthen the unobserved cell fixture at line 129. |
| test_activation_scoring.py | 21 | 21 | 48 | Keep most. Consolidate the identical judge-error fixture and the shared opportunity fixture while retaining all assertions. |
| test_compare.py | 16 | 16 | 55 | Strengthen empty/vacuous matching checks and literal numeric/secondary-rendering expectations. No immediate deletion justified. |
| test_compare_cli.py | 7 | 8 | 16 | Keep. Reference resolution, refusal, good controls and format rejection are actual command behavior. |
| test_diagnosis.py | 19 | 20 | 30 | One safe deletion of the interrupted enum pin. Keep the unexercised but published reserved code; strengthen actual title/panel observations. |
| test_reporter.py | 39 | 39 | 69 | Keep rendering, markup and regression checks. Consolidation is possible without removing input scenarios. Strengthen truncation tail/limit assertions. |
| test_runstore.py | 30 | 38 | 33 | Keep distinct filesystem and addressing branches. Strengthen payload round trip and same-second preservation checks. |
| test_scoring.py | 12 | 12 | 27 | Keep real formulas, roll-up and dual-scoreboard contracts. Conditional consolidation of overlapping unmeasured averages only. |
| test_skill_drift.py | 9 | 9 | 19 | Keep. Two pairs have identical fixtures and can consolidate all their assertions. Strengthen digest and source-kind observations. |
| test_task_metrics.py | 21 | 21 | 38 | One safe duplicate deletion at line 123. Two further trigger-only overlaps are conditional because assertion metadata differs. Keep serialization, tampering and legacy load contracts. |
| test_transcript_persistence.py | 3 | 3 | 10 | Keep nested persistence path. Combine the identical legacy AttemptRecord fixture with the legacy usage test if retaining both optional-field assertions. |
| test_usage.py | 26 | 29 | 62 | Keep normalization, totals and deltas. Strengthen row-scoped rendering, absent-token and server-ablation verdict checks. |
| Total | 245 | 265 | 576 | Two immediate candidates, plus conditional consolidations and strengthening work. |

## Components and flow

`caliper/schema/results.py` owns outcome categories, literal metric formulas, derived TaskResult counts/rates, both AggregateScore scoreboards, usage totals, persistence models and comparison result types. These are public result/JSON contracts. Constructing a TaskResult from known attempts and checking a derived property is an invocation of production logic. It is not a fixture checking itself merely because no runner is involved.

`caliper/activation.py:61` observes normalized ConversationTurn inputs. Dedicated names and path suffixes are a union. Unknown declared names, directory listings, file-prefix collisions, no installed skills, and exact-set assertions have different semantics. None means unobserved/unasserted; an empty set is a measured silence. Those absence assertions protect behavior.

`caliper/compare.py:225` reads TaskResult.score and activation_score into TaskComparison, copying both outcome lists. `diff_runs` at line 308 matches names, pairs duplicate names positionally, averages only tasks measured on both sides, warns on confounds, refuses cross-era comparisons, and computes usage over whole runs. The pure result is consumed by both the renderer and JSON output. Most tests that inspect RunComparison fields are testing the public comparison contract rather than implementation choreography.

`caliper/reporter.py:634` reads the models, renders the primary task table, secondary metrics under verbose, both scoreboards, cost totals and per-usable-attempt cost, skill/MCP diagnostics, notes and failure detail panels. `_format_output` at line 1084 truncates raw output before escaping. This escaping order and the literal no-output marker were real regressions. `_print_secondary_metrics` at line 1482 invokes `_alt_metric`; the secondary formula relation test in test_compare.py never invokes either renderer function.

`caliper/runstore.py` owns real filesystem persistence and run reference discovery. `RunStore.save` validates the spec and root, writes exclusively, and chooses timestamp suffixes. Resolve branches for explicit JSON, eval spec path, named run and latest run are all distinct. Discovery is bounded by the repository and can deliberately choose a nearer root. The command tests use actual Typer routing and a real store with small fixture JSON files, rather than mocking these paths.

`caliper/commands/diagnosis.py:104` converts actual error types into a public Diagnosis, and fail/render print and raise the selected exit. RunAborted recurses into its cause. A directly asserted symbolic error mapping is a meaningful model contract. `caliper/commands/run.py:325` handles an interrupted result by raising `typer.Exit(ExitCode.INTERRUPTED)`.

Backend usage tests feed literal wire events to actual `_read` methods. Claude uses its first result usage; Codex uses latest turn.completed usage, subtracts cached input, and does not add reasoning output a second time; pi sums assistant message usage; Hermes reads session totals. AgentReport.usage goes through `_best_effort` in `caliper/harness/base.py:264`. Tests of missing or malformed usage legitimately assert None because optional provenance must not sink an attempt.

## Safe deletion candidates

1. `tests/test_task_metrics.py:123`, `test_a_task_with_a_real_verdict_is_not_trigger_only`.

   Its only assertion is `_task(Outcome.PASS, Outcome.NOT_CHECKED).trigger_only is False`. The exact surviving test is `tests/test_not_checked.py:193`, also named `test_a_task_with_a_real_verdict_is_not_trigger_only`. It constructs the same ordered PASS and NOT_CHECKED outcome vector, has the same activation_expected=None, invokes the same TaskResult.trigger_only getter and asserts the same literal False. Task ids, names and duration differ; none participates in this predicate or its documented meaning. The same malformed trigger-only classification would fail the survivor. No known behavioral assertion is lost. This was initially inspected equivalence. The parent then ran a process-local mutation making trigger_only always True; the retained test_not_checked.py survivor failed at its literal False assertion. I inspected `docs/qa/test-suite-audit-2026-10-10/mutant-trigger.txt`, which reports one failure in 0.11s. This proves that survivor detects this particular misclassification, without a general mutation-score claim. Keep the survivor in the final combined cut list.

2. `tests/test_diagnosis.py:171`, `test_the_interrupt_code_is_named`.

   Its only assertion is `ExitCode.INTERRUPTED == 130`. The exact surviving test is `tests/test_cancel.py:680`, `test_run_cli_exits_130_and_saves_an_interrupted_run`. That test invokes the real run command with an interrupted RunResults, reaches the unmocked command branch at `caliper/commands/run.py:325`, accesses the same ExitCode.INTERRUPTED member and asserts literal exit_code=130 plus interrupted output and one saved file. Neither the enum nor Typer exit is mocked. A missing member or changed integer cannot pass that survivor. `tests/test_run_cli.py:447`, `test_run_cli_keeps_report_and_interrupt_exit_if_output_cannot_be_written`, is an additional surviving interrupted-exit check under a different write-failure input. No current interrupted code contract is lost; only a direct constant pin is removed. Keep both behavior tests, particularly the test_cancel.py survivor, rather than using this as a reason to cut cancellation tests.

These two cuts remove two functions and two cases. They do not justify a broad deletion of schema properties or enum checks. For comparison, test_diagnosis.py:166 pins reserved BAR_NOT_MET=3, which no actual command emits. Git history confirms this was a deliberate published reservation in commit 765a1e7, #95. There is no surviving runtime behavior test for that reservation, so it is not a safe-delete candidate under this audit contract.

## Conditional consolidation candidates

Every consolidation below is conditional on retaining the named assertions and input cases. Deleting the narrower test now would lose something.

- `tests/test_activation_scoring.py:100`, `test_execution_and_activation_denominators_differ`, can merge into line 69, `test_judge_error_attempts_still_count_toward_activation`. Both construct exactly JUDGE_ERROR and PASS attempts, observed=['mine'], activation_passed=True and expected=['mine']. Line 69 already checks activation_usable=2 and activation_score=1.0. Add line 100's literal usable=1 before deleting it. Without that addition, the explicit execution denominator assertion is lost.
- `tests/test_activation_scoring.py:279`, `test_opportunities_is_the_denominator_the_rate_divides_by`, can merge into line 212, `test_a_hijacker_has_full_recall_and_a_high_over_firing_rate`. Both build total=9, expected=6, fired=8, hits=6. Keep opportunities=3 and the literal approximate rate 2/3 from the surviving test. The ratio assertion currently uses two other properties as its oracle, so paired defects in unwanted and unwanted_rate can agree. An immediate deletion would drop the direct opportunities field check, which the reporter consumes.
- `tests/test_skill_drift.py:86`, `test_a_drifted_git_source_reports_the_two_commits`, can merge into line 75, `test_a_drifted_git_source_warns`. The fixtures are identical, including commits, text, source kind and timestamp offset. Move a_ref='aaaaaaa' and b_ref='bbbbbbb' into the warning test. Current line 75 asserts record identity/type/warning but not commit handles.
- `tests/test_skill_drift.py:108`, `test_a_path_source_falls_back_to_a_content_digest`, can merge into line 96, `test_a_drifted_path_source_is_recorded_but_does_not_warn`. Again the fixtures are identical. Retain distinct short refs and their length, preferably replace with literal expected hashes. Immediate deletion loses short-handle assertions.
- `tests/test_transcript_persistence.py:17`, `test_old_results_json_without_transcript_still_loads`, and `tests/test_usage.py:258`, `test_old_results_json_without_usage_still_loads`, validate exactly the same four-key old AttemptRecord JSON. Keep both `transcript is None` and `usage is None`, plus passed=True, in one backward-compatibility test. Either immediate deletion loses one optional-field contract.
- `tests/test_task_metrics.py:111`, `test_a_task_with_no_execution_check_is_trigger_only`, overlaps `tests/test_not_checked.py:136`, `test_trigger_only_task_is_detected`, for the same two NOT_CHECKED outcomes. The former omits activation_expected; the latter sets expected=[]. Preserve both metadata variants if consolidating. A future defect that requires authored activation metadata would pass the latter and fail the former.
- `tests/test_task_metrics.py:115`, `test_trigger_only_survives_one_timeout_among_k`, overlaps `tests/test_not_checked.py:173` with the same name and NOT_CHECKED/TIMEOUT outcomes. The latter additionally checks `_success_cell(...).plain.split()==['—','probe']`, but expected=[] differs from the former's expected=None. Preserve both metadata inputs when consolidating; the rendering case is worth keeping because it records a regression.
- `tests/test_reporter.py:313`, `test_empty_output_renders_no_output_marker`, overlaps line 582, `test_empty_output_marker_is_visible_when_markup_is_rendered`. The same blank failed attempt goes through print_results; the latter has stronger literal '[no output]' assertions. The console configurations differ, markup=False versus enabled markup. Consolidate only if that distinction is retained or explicitly judged outside the supported contract. The enabled-markup case is a known Rich swallowed-marker regression, so retain it.
- `tests/test_reporter.py:210`, `test_failed_task_assert_evidence_shown_by_default`, and line 218, `test_failed_task_output_shown_by_default`, overlap line 256, `test_only_failed_tasks_shown_in_mixed_results`. The mixed test checks both failure output and evidence, plus passing output suppression. The singleton tests exercise only-one-task input, with different literal text. Consolidate with both singleton and mixed cases if wanted; do not claim the mixed fixture subsumes every singleton behavior as written.
- `tests/test_reporter.py:248`, `test_passing_task_detail_not_shown_by_default`, line 279, `test_verbose_shows_passing_task_detail`, and line 287, `test_verbose_shows_all_tasks`, can be part of the same output visibility matrix as line 256. Keep the default/verbose switch and singleton/mixed input cases. The switch exercises `_needs_detail` and the all-tasks verbose branch, so removing one mode loses meaningful behavior. A matrix may reduce setup repetition without reducing case runtime.
- `tests/test_scoring.py:85`, `test_average_ignores_none_scores`, overlaps line 69, `test_a_fully_unusable_task_scores_none_and_leaves_the_average_alone`. One uses JUDGE_ERROR and a nonperfect 0.5 measured rate; the other uses INFRA_ERROR/TIMEOUT and a perfect 1.0 rate. Consolidate via explicit outcome/rate cases if desired. The noise categories and nonperfect rate are different inputs, so this is not a safe immediate deletion.

## Strengthening candidates and gaps

1. `tests/test_compare.py:284`, `test_the_secondary_columns_agree_with_the_task_metrics`, does not call print_comparison. Its assertions are `pass_at_k(*_counts(tc.b_outcomes)) == task.pass_at_k` and the same relation for pass_hat_k. The right side uses exactly the same production formulas, so a wrong formula can agree. The test still detects some B outcome propagation errors, which is real signal; it is not a safe deletion. Assert the exact copied outcomes and literal rates, and render `verbose=True` on an input with unusable noise. Row-scoped literal secondary values would test the claimed column contract.

2. The matching smoke at `tests/test_compare.py:71`, `test_identical_runs_have_zero_deltas_and_no_regression`, uses all(...) twice without asserting nonempty matched rows. An implementation that produces no matched or unmatched tasks with zero aggregates passes every assertion. Add exact matched names, scores and deltas. Line 90, `test_single_task_regression_flags_only_that_task`, and line 106, `test_improvement_is_not_flagged_as_regression`, check delta signs instead of exact -0.6/+0.6 and the paired scores; use literal expected values. Their regression routing assertions should stay.

3. `tests/test_compare.py:135`, `test_matching_is_by_name_not_positional_id`, reorders names but gives every task the same default task_id='task-000'. It does catch many positional matching errors, but the comment claiming positional ids differ is inaccurate. Give A and B different per-task ids, and assert matched order and names along with literal scores/deltas.

4. `tests/test_compare.py:268`, `test_verbose_comparison_renders_the_secondary_metric_columns`, only checks the two headings. Keep this test because commit 2695468, #93, explicitly records a real dead scorer import in the verbose renderer. Strengthen with literal row values for both sides and both metrics. With its current 2/4 and 3/4 outcomes, expected pass@k is 93.8% and 99.6%, and pass^k is 6.2% and 31.6% at reporter precision. Do not delete this as a heading-only smoke; it executes a historically uncovered path.

5. `tests/test_compare.py:301`, `test_no_comparable_task_shows_no_overall_delta`, asserts the literal explanatory phrase, which is good, but still forbids old '+0.0%' text although current deltas are percentage points. Assert the actual Success row has dashes and no fabricated percentage/delta. Git commit ba19b31, #173, documents a real misleading empty-average regression.

6. `tests/test_activation_report.py:129`, `test_unobserved_activation_renders_as_a_dash`, supplies expected=None, so `_activation_cell` exits before examining activated=None. It mostly repeats unasserted rendering at line 103. Use expected=['mine'] and activated=None, with activation_passed=None, to actually test an asserted but unobserved column. Preserve the line 103 dim-unasserted contract separately.

7. `tests/test_diagnosis.py:49` and line 53 have names claiming exit one/two but assert only symbolic enum identity. A mutation swapping the integer values of BAD_INPUT/CANNOT_RUN while leaving symbolic references consistent can survive these tests. CLI behavior checks elsewhere do catch many real integer codes. A better direct oracle is a Diagnosis containing literal numeric code, title and body for one input. Keep the distinct error type mappings, especially unknown exception, LoginRequired and RunAborted causes. Lines 138 and 144 similarly claim panel versus one-line rendering but inspect only Diagnosis.title. Invoke render/fail and assert actual output shape/title rather than assuming the metadata was consumed correctly.

8. `tests/test_reporter.py:184`, `test_format_output_short_string_unchanged`, allows any prefix/suffix as long as 'hello world' remains and 'truncated' is absent. The function contract can be asserted with literal equality. Lines 190, 198 and 304 import `_OUTPUT_TRUNCATE_AT` to size fixtures and expected output. They test the mechanism across a boundary but do not pin any intended public limit. That is not automatically wrong if the threshold is an implementation choice. However homogeneous x/z text cannot distinguish the retained tail from the head, and >=500 z permits excess untruncated text. Use a distinct discarded prefix and literal surviving suffix; assert discarded content is absent and exact retained size. Preserve line 198's boundary-equality scenario.

9. `tests/test_reporter.py:571`, `test_truncating_escaped_markup_cannot_expose_a_tag`, is worth keeping. History 6e927d3 explicitly describes truncating escaped output dropping the backslash and Rich swallowing markers. Add a literal truncation marker/tail check if strengthening, but do not cut it as a constant-derived fixture. Line 487's raw-markup rendering and line 582's literal marker are also real display regressions.

10. `tests/test_reporter.py:609`, `test_a_finished_task_counts_like_its_attempts_did_live`, asserts dataclass equality against OutcomeCounts(outcomes) constructed in the same test. This compares the production counts property to the same constructor it uses internally. It tests extraction but not the claimed live/final display parity. Add literal counts or drive live progress and final output for the same outcomes. Unlike a fixture assertion, production TaskResult.counts is called; deleting it immediately would lose this extraction check.

11. `tests/test_runstore.py:29`, `test_save_round_trips_through_load`, asserts only loaded.run.spec. The payload can lose non-spec fields and still satisfy it. Use an explicit record with distinct metadata and attempts, and assert equality or literal fields after load. Round-trip equality is acceptable here because independent serialization/deserialization actually runs; stronger literal checks identify the external field meanings. Line 68, same-second survival, checks existence and distinct paths but writes identical payloads. Use distinguishable attempt contents on both files and load them back to catch partial overwrite/cross-file content bugs. Keep the literal '-2' naming and the 11-run numeric ordering at line 80; the latter covers '-10' versus '-2', not just a duplicate of two saves.

12. `tests/test_skill_drift.py:108` checks unequal refs and length 7 without proving the handle uses the captured hashes; unrelated strings could pass. Literal short digest expectations from an independent fixture oracle would be stronger. Line 152, source-kind change, merely asserts some warnings exist. Assert the warning names tdd and git provenance so a generic unrelated warning cannot satisfy it.

13. `tests/test_usage.py:339`, `test_print_results_renders_per_task_tokens_and_wall`, accepts '2.0M' and '24s' anywhere. The same values occur in Overall, and tokens also occur in a note. The test can pass with a missing or wrong task cost cell. Assert the alpha row contains both values. Line 472, `test_print_comparison_renders_usage_rows`, checks headings only; assert literal side totals, per-attempt cost and -40% deltas in the Tokens/Wall rows. These should remain renderer contract tests, not be replaced solely by schema-property checks.

14. `tests/test_usage.py:460`, `test_print_results_per_task_tokens_dash_when_unreported`, never asserts a token dash. It checks only '3s' anywhere, which appears in Overall as well. Assert the alpha row's dash plus 3s and absence of a false token total. The missing-data model test at line 240 is not a substitute for rendered absence.

15. `tests/test_usage.py:404`, `test_a_server_ablated_run_keeps_its_activation_verdicts`, creates activation_expected=['keeper'] but no activation_passed flag and its `_run` helper supplies no activation aggregate. It checks ablation labels and absence of skill-ablation caveats, but the asserted headline/verdict behavior is never created or asserted. Give the attempt activation_passed=True, construct AggregateScore.from_task_results, and assert the preserved activation cell/rate. This is a fixture setup gap, not evidence that the server-ablation behavior itself is unneeded.

16. `tests/test_usage.py:348`, `test_every_run_renders_a_single_report`, claims one table but checks only absence of 'compare' and presence of Overall. Printing two ordinary reports satisfies it. If report multiplicity matters, assert exactly one Overall row/header. `tests/test_usage.py:432`, `test_a_normal_run_has_no_observed_activations_block`, supplies no observations or declared names, so it would also pass if an erroneous normal-run observation renderer is called but suppresses an empty table. Use a non-ablated run with a real observed skill to make absence meaningful. Keep its positive ablated counterpart at line 371.

17. Transcript round trips at `tests/test_transcript_persistence.py:29` and line 63 are genuine schema persistence calls. They cover different nesting paths and outcomes. Their partial assertions can strengthen to exact ordered turns/roles, content and tool fields; do not treat restored tool_input as fixture checking itself. The deserialize result is the subject output, and optional defaults/unknown older keys are compatibility contracts.

18. Several fixture helpers remain dated. `test_compare.py:_run`, `test_reporter.py:_make_results` and `test_usage.py:_run` pass singular `skill_snapshot`, which RunResults now ignores as an unknown legacy key, leaving actual skill_snapshots empty. They also build aggregate.avg_score from pass_at_k rather than primary score, and some provide empty per_task, which backfills scored_tasks=0. Current assertions often still test their intended failure output or usage, so this is not a blanket delete reason. Replace those helper aggregates with AggregateScore.from_task_results and plural snapshots if strengthening; then check that intended output tests still address the same behavior.

## Contracts that should stay

- SkillActivationStats recall, precision, opportunities and unwanted_rate tests run actual arithmetic over literal counters. They are public derived JSON and reporter inputs, not assertions that a fixture contains its own raw fields. Only the opportunity ratio oracle needs consolidation/strengthening. The 1/10 versus 1/2 case at test_activation_scoring.py:188 distinguishes rate denominators that users otherwise confuse.
- TaskResult serialization and tamper tests at test_task_metrics.py:73 and :85 protect computed fields and ignoring stale stored counts. These are meaningful persistence guarantees, unlike checking a fixture dictionary before calling model_validate.
- Legacy missing optional usage/transcript fields, old AggregateScore scored_tasks backfill, unknown legacy skill_snapshot and legacy era comparisons remain supported in production. Keep their compatibility checks unless support is deliberately retired. Current two-legacy comparisons are a distinct guard input from two current runs; cross-era refusal does not subsume it.
- Activation None/empty-set distinctions, all-noise execution scores and no-comparable aggregates prevent fabricated negative measurements. Exception and absence assertions here are required boundary contracts.
- RunStore discovery, actual CLI compare/list addressing, corruption and impossible-number rejection should stay despite setup cost. A unit test of a convenience property does not replace a real load/discovery/command path. Negative controls for missing JSON, missing named run, unknown spec and empty spec are distinct calls with distinct future failure modes.
- MCP helper tests in reporter.py feed actual transcript roles, prefixes and known server names, then assert exact attribution tuple values. Their use of an internal helper is narrow but the output is the real diagnostics contract. Longest server name and leading underscore are backend protocol edge cases, not constant pins.
- Progress tests inspect Rich Task completed counts and literal glyph sequences. Those are actual visible progress state and preserve finish-order, early-stop, neutral probes and tally behavior. They do not assert calls to a mocked progress method.

## Boundaries, evidence limits and open questions

No global coverage conclusion is made. Overlap outside this slice was inspected only for named survivors or history. The parent must ensure that the selected survivor is not also deleted by another slice, especially test_not_checked.py:193 and test_cancel.py:680.

Only the parent-provided targeted trigger mutation log was inspected as executed evidence. It does not prove all duplicate equivalence or a suite-wide mutation score. The other defects that assertions would logically miss or catch are inferred from inspected call paths. A coordinated in-memory mutation or selective test run could later validate these claims, but no instrumentation is proposed or performed during this read-only slice.

The direct reserved exit code test costs almost nothing and documents an intentional unimplemented published reservation. Its value is a policy choice the maintainer can make; no actual behavioral survivor exists today. Keep it in the immediate list.

The historical prose around force-loaded legacy eras says older semantics are not comparable across backends, while test_two_legacy_runs_still_compare covers same-backend fixtures only. That does not justify cutting the legacy test or asserting support for arbitrary cross-backend legacy comparison. Such semantics would need an explicit production change outside this audit.

Strict public convenience property input coverage matters. The trigger-only duplicates with expected=None versus expected=[] cannot automatically be collapsed because both current and older saved runs call those getters with different metadata. The one safe identical-outcome negative control uses expected=None on both sides. Counts/duration/name fields outside the predicate are irrelevant to that particular public claim, but no global assertion that all TaskResult metadata is irrelevant is made.

## Files read

Read in full: all thirteen assigned test files, tests/conftest.py, tests/test_not_checked.py, .github/CONTRIBUTING.md, the audit contract, principle-test-behavior-not-implementation/SKILL.md, how/SKILL.md and its explorer template, unslop/SKILL.md, pyproject.toml, caliper/schema/results.py, caliper/activation.py, caliper/runstore.py, caliper/commands/diagnosis.py, caliper/commands/compare.py, caliper/commands/report.py and caliper/commands/list_cmd.py. Read relevant complete implementations in caliper/compare.py and caliper/reporter.py, including comparison, outcome/progress/activation/cost renderers, notes, headers, output formatting and detail panels.

Read focused implementation excerpts: caliper/harness/base.py AgentReport.usage and _best_effort; caliper/harness/claude_code.py _read/_usage; caliper/harness/codex.py _read/_usage; caliper/harness/pi.py _read_stream/_usage; caliper/harness/hermes.py _read/_usage; caliper/commands/run.py interrupted exit branch.

Read focused overlap excerpts: tests/test_cancel.py:650-704; tests/test_run_cli.py:345-375, :429-472 and :670-723; tests/test_skillsnapshot.py:127-151. Read or searched relevant context in tests/test_measure_and_mark.py, tests/test_user_layer.py, tests/test_doc_sync.py, tests/test_run_seam.py and tests/test_ablation.py; their full modules were not audited. Production docs consulted were ADR 0007, 0013, 0014, 0017, 0021 and 0022, some as focused excerpts. Git log/blame/show inspected 2695468, 674b6f5, 765a1e7, 6e927d3 and ba19b31 at the audited ancestry. Also inspected parent-run artifacts mutant-trigger.txt and pruning-probe.txt/pruning-probe-six.txt.

## Assertion ledger

Each entry below records every assert in the assigned test body and every direct raises check. The disposition reflects the source audit above. KEEP entries include boundary negative controls and schema contracts even when their assert shape superficially resembles a weak pattern. A CONDITIONAL entry must retain the described distinct assertion/input before any deletion.

### tests/test_activation.py

- `test_detects_claude_codes_skill_tool_call`, tests/test_activation.py:21. KEEP. Actual transcript detector/set-verdict boundary, checked against literal observed activation or verdict.
  - Assert at line 24: `detector.detect(turns) == ['unit-normalizer']`.

- `test_detects_hermes_skill_view_call`, tests/test_activation.py:27. KEEP. Actual transcript detector/set-verdict boundary, checked against literal observed activation or verdict.
  - Assert at line 30: `detector.detect(turns) == ['unit-normalizer']`.

- `test_dedicated_tool_naming_an_undeclared_skill_is_ignored`, tests/test_activation.py:33. KEEP. Actual transcript detector/set-verdict boundary, checked against literal observed activation or verdict.
  - Assert at line 36: `detector.detect([tool('Skill', {'skill': 'something-else'})]) == []`.

- `test_detects_an_absolute_path_read`, tests/test_activation.py:42. KEEP. Actual transcript detector/set-verdict boundary, checked against literal observed activation or verdict.
  - Assert at line 47: `detector.detect(turns) == ['unit-normalizer']`.

- `test_detects_the_same_activation_written_as_a_relative_path`, tests/test_activation.py:50. KEEP. Actual transcript detector/set-verdict boundary, checked against literal observed activation or verdict.
  - Assert at line 60: `detector.detect(turns) == ['unit-normalizer']`.

- `test_listing_the_skills_root_is_not_activation`, tests/test_activation.py:63. KEEP. Actual transcript detector/set-verdict boundary, checked against literal observed activation or verdict.
  - Assert at line 65: `detector.detect([tool('shell', {'command': 'ls .codex/skills/'})]) == []`.

- `test_a_similarly_named_skill_does_not_match`, tests/test_activation.py:68. KEEP. Actual transcript detector/set-verdict boundary, checked against literal observed activation or verdict.
  - Assert at line 71: `detector.detect(turns) == []`.

- `test_a_path_that_only_starts_with_the_skill_file_does_not_match`, tests/test_activation.py:82. KEEP. Actual transcript detector/set-verdict boundary, checked against literal observed activation or verdict.
  - Assert at line 84: `detector.detect([tool('shell', {'command': command})]) == []`.

- `test_the_skill_file_still_matches_wherever_its_path_ends`, tests/test_activation.py:98. KEEP. Actual transcript detector/set-verdict boundary, checked against literal observed activation or verdict.
  - Assert at line 100: `detector.detect([tool('shell', {'command': command})]) == ['alpha']`.

- `test_a_direct_file_read_counts_even_where_a_dedicated_tool_exists`, tests/test_activation.py:106. KEEP. Actual transcript detector/set-verdict boundary, checked against literal observed activation or verdict.
  - Assert at line 112: `detector.detect(turns) == ['mine']`.

- `test_result_is_a_deduplicated_sorted_set`, tests/test_activation.py:115. KEEP. Actual transcript detector/set-verdict boundary, checked against literal observed activation or verdict.
  - Assert at line 122: `detector.detect(turns) == ['a-skill', 'b-skill']`.

- `test_no_tool_calls_means_nothing_activated`, tests/test_activation.py:125. KEEP. Actual transcript detector/set-verdict boundary, checked against literal observed activation or verdict.
  - Assert at line 128: `detector.detect(turns) == []`.

- `test_detection_is_unavailable_when_no_skills_are_declared`, tests/test_activation.py:131. KEEP. Actual transcript detector/set-verdict boundary, checked against literal observed activation or verdict.
  - Assert at line 135: `ActivationDetector([], frozenset({'Skill'})).detect([]) is None`.

- `test_exact_match_passes`, tests/test_activation.py:141. KEEP. Actual transcript detector/set-verdict boundary, checked against literal observed activation or verdict.
  - Assert at line 142: `check_activation(['a'], ['a']) is True`.

- `test_a_missing_expected_skill_fails`, tests/test_activation.py:145. KEEP. Actual transcript detector/set-verdict boundary, checked against literal observed activation or verdict.
  - Assert at line 146: `check_activation([], ['a']) is False`.

- `test_an_extra_unexpected_skill_fails`, tests/test_activation.py:149. KEEP. Actual transcript detector/set-verdict boundary, checked against literal observed activation or verdict.
  - Assert at line 150: `check_activation(['a', 'b'], ['a']) is False`.

- `test_order_does_not_matter`, tests/test_activation.py:153. KEEP. Actual transcript detector/set-verdict boundary, checked against literal observed activation or verdict.
  - Assert at line 154: `check_activation(['b', 'a'], ['a', 'b']) is True`.

- `test_expected_silence_holds_when_nothing_fired`, tests/test_activation.py:157. KEEP. Actual transcript detector/set-verdict boundary, checked against literal observed activation or verdict.
  - Assert at line 158: `check_activation([], []) is True`.

- `test_expected_silence_fails_when_something_fired`, tests/test_activation.py:161. KEEP. Actual transcript detector/set-verdict boundary, checked against literal observed activation or verdict.
  - Assert at line 162: `check_activation(['a'], []) is False`.

- `test_not_asserted_yields_no_verdict`, tests/test_activation.py:165. KEEP. Actual transcript detector/set-verdict boundary, checked against literal observed activation or verdict.
  - Assert at line 166: `check_activation(['a'], None) is None`.

- `test_unobserved_activation_yields_no_verdict`, tests/test_activation.py:169. KEEP. Actual transcript detector/set-verdict boundary, checked against literal observed activation or verdict.
  - Assert at line 171: `check_activation(None, ['a']) is None`.

### tests/test_activation_report.py

- `test_column_is_a_verdict_per_attempt_not_a_list_of_names`, tests/test_activation_report.py:67. KEEP. Actual Rich cell/full report or public comparison contract; literal rate, mark, style or guard observation.
  - Assert at line 79: `cell.plain.split() == ['66.7%', '✓', '✗', '✓']`.
  - Assert at line 80: `'mine' not in cell.plain`.

- `test_column_fails_when_the_agent_reached_for_nothing`, tests/test_activation_report.py:83. KEEP. Actual Rich cell/full report or public comparison contract; literal rate, mark, style or guard observation.
  - Assert at line 86: `cell.plain.split() == ['0%', '✗']`.
  - Assert at line 87: `'red' in _style_of(cell, '0%')`.

- `test_a_fully_timed_out_task_shows_no_claim_about_the_skill`, tests/test_activation_report.py:90. KEEP. Actual Rich cell/full report or public comparison contract; literal rate, mark, style or guard observation.
  - Assert at line 100: `_activation_cell(t, k=2).plain.split() == ['—', '⊘', '⊘']`.

- `test_column_is_dim_when_nothing_was_asserted`, tests/test_activation_report.py:103. KEEP. Actual Rich cell/full report or public comparison contract; literal rate, mark, style or guard observation.
  - Assert at line 106: `cell.plain.strip() == '—'`.
  - Assert at line 107: `cell.style == 'dim'`.

- `test_a_held_assertion_is_marked_green`, tests/test_activation_report.py:110. KEEP. Actual Rich cell/full report or public comparison contract; literal rate, mark, style or guard observation.
  - Assert at line 115: `'red' not in _style_of(cell, '100%')`.
  - Assert at line 116: `'green' in _style_of(cell, '✓')`.

- `test_column_is_red_when_a_neighbour_hijacked`, tests/test_activation_report.py:119. KEEP. Actual Rich cell/full report or public comparison contract; literal rate, mark, style or guard observation.
  - Assert at line 125: `cell.plain.split() == ['0%', '✗']`.
  - Assert at line 126: `'red' in _style_of(cell, '✗')`.

- `test_unobserved_activation_renders_as_a_dash`, tests/test_activation_report.py:129. STRENGTHEN. expected=None exits before unobserved branch; use asserted expected list with unavailable activation.
  - Assert at line 131: `_activation_cell(t, k=1).plain.strip() == '—'`.

- `test_report_prints_both_scoreboards_separately`, tests/test_activation_report.py:152. KEEP. Actual Rich cell/full report or public comparison contract; literal rate, mark, style or guard observation.
  - Assert at line 173: `'success' in header`.
  - Assert at line 174: `'activation' in header`.
  - Assert at line 176: `'100%' in overall`.
  - Assert at line 177: `'73.3%' in overall`.
  - Assert at line 179: `re.search('mine\\s.*100%\\s+2/2\\s.*50%\\s+2/4', out)`.

- `test_a_timed_out_attempt_shows_what_it_activated_and_its_error`, tests/test_activation_report.py:182. KEEP. Actual Rich cell/full report or public comparison contract; literal rate, mark, style or guard observation.
  - Assert at line 189: `re.search('activated so far\\s+sleeper', out)`.
  - Assert at line 190: `re.search('error\\s+timeout', out)`.
  - Assert at line 191: `not re.search('assert\\s+timeout', out)`.

- `test_activation_line_is_absent_when_nothing_was_asserted`, tests/test_activation_report.py:194. KEEP. Actual Rich cell/full report or public comparison contract; literal rate, mark, style or guard observation.
  - Assert at line 197: `'success' in out`.
  - Assert at line 198: `'activation' not in out.lower()`.

- `test_compare_refuses_a_cross_era_diff`, tests/test_activation_report.py:221. KEEP. Actual Rich cell/full report or public comparison contract; literal rate, mark, style or guard observation.
  - Exception contract at line 224: `pytest.raises(IncomparableRunsError)`.
  - Assert at line 226: `'force-loaded' in str(exc.value)`.

- `test_compare_allows_two_runs_of_the_same_era`, tests/test_activation_report.py:229. KEEP. Actual Rich cell/full report or public comparison contract; literal rate, mark, style or guard observation.
  - Assert at line 233: `comp.neighbourhood_mismatch is False`.
  - Assert at line 234: `comp.warnings == []`.

- `test_compare_warns_but_still_renders_on_a_neighbourhood_change`, tests/test_activation_report.py:237. KEEP. Actual Rich cell/full report or public comparison contract; literal rate, mark, style or guard observation.
  - Assert at line 242: `comp.neighbourhood_mismatch is True`.
  - Assert at line 243: `any(('neighbourhood' in w for w in comp.warnings))`.
  - Assert at line 244: `len(comp.matched) == 1`.

- `test_two_legacy_runs_still_compare`, tests/test_activation_report.py:247. KEEP. Actual Rich cell/full report or public comparison contract; literal rate, mark, style or guard observation.
  - Assert at line 250: `len(comp.matched) == 1`.

- `test_compare_surfaces_an_activation_delta_for_a_trigger_only_task`, tests/test_activation_report.py:290. KEEP. Actual Rich cell/full report or public comparison contract; literal rate, mark, style or guard observation.
  - Assert at line 297: `tc.a_score is None`.
  - Assert at line 298: `tc.b_score is None`.
  - Assert at line 299: `tc.a_activation == 1.0`.
  - Assert at line 300: `tc.b_activation == 0.25`.
  - Assert at line 301: `tc.activation_delta == -0.75`.
  - Assert at line 302: `tc.activation_regression is True`.

- `test_activation_regression_is_separate_from_execution_regression`, tests/test_activation_report.py:305. KEEP. Actual Rich cell/full report or public comparison contract; literal rate, mark, style or guard observation.
  - Assert at line 307: `comp.has_activation_regression is True`.
  - Assert at line 310: `comp.has_regression is False`.

- `test_no_activation_regression_when_the_description_improves`, tests/test_activation_report.py:313. KEEP. Actual Rich cell/full report or public comparison contract; literal rate, mark, style or guard observation.
  - Assert at line 316: `tc.activation_delta == 1.0`.
  - Assert at line 317: `tc.activation_regression is False`.
  - Assert at line 318: `comp.has_activation_regression is False`.

- `test_execution_headline_is_skipped_when_nothing_was_measured`, tests/test_activation_report.py:321. KEEP. Actual Rich cell/full report or public comparison contract; literal rate, mark, style or guard observation.
  - Assert at line 353: `'no execution checks' in out`.
  - Assert at line 355: `'success' not in out`.
  - Assert at line 357: `'activation' in out`.

- `test_overall_shows_the_pooled_count_when_it_agrees_with_the_average`, tests/test_activation_report.py:368. KEEP. Actual Rich cell/full report or public comparison contract; literal rate, mark, style or guard observation.
  - Assert at line 375: `re.search('75%\\s+3/4 ✓', overall)`.

- `test_overall_names_what_it_averages_when_the_count_would_disagree`, tests/test_activation_report.py:378. KEEP. Actual Rich cell/full report or public comparison contract; literal rate, mark, style or guard observation.
  - Assert at line 387: `re.search('66.7%\\s+avg of 2', overall)`.
  - Assert at line 388: `'2/4' not in overall`.

- `test_a_finished_attempt_does_not_claim_it_stopped`, tests/test_activation_report.py:391. KEEP. Actual Rich cell/full report or public comparison contract; literal rate, mark, style or guard observation.
  - Assert at line 397: `'attempt 1' in out`.
  - Assert at line 398: `'activated so far' not in out`.

### tests/test_activation_scoring.py

- `test_a_task_asserting_nothing_is_skipped_not_scored_zero`, tests/test_activation_scoring.py:52. KEEP. Public result arithmetic or aggregate constructor derives values from concrete attempts/counters; legitimate model contract.
  - Assert at line 54: `t.activation_score is None`.
  - Assert at line 56: `agg.avg_activation_score is None`.
  - Assert at line 57: `agg.activation_tasks == 0`.

- `test_exact_match_across_attempts_scores_one`, tests/test_activation_scoring.py:60. KEEP. Public result arithmetic or aggregate constructor derives values from concrete attempts/counters; legitimate model contract.
  - Assert at line 65: `t.activation_score == 1.0`.
  - Assert at line 66: `_activation([t]).avg_activation_score == 1.0`.

- `test_judge_error_attempts_still_count_toward_activation`, tests/test_activation_scoring.py:69. KEEP. Public result arithmetic or aggregate constructor derives values from concrete attempts/counters; legitimate model contract.
  - Assert at line 76: `t.activation_usable == 2`.
  - Assert at line 77: `t.activation_score == 1.0`.

- `test_timeouts_and_infra_errors_are_excluded_from_activation`, tests/test_activation_scoring.py:80. KEEP. Public result arithmetic or aggregate constructor derives values from concrete attempts/counters; legitimate model contract.
  - Assert at line 88: `t.activation_usable == 1`.
  - Assert at line 89: `t.activation_score == 1.0`.

- `test_a_task_whose_every_attempt_timed_out_is_unmeasured_not_zero`, tests/test_activation_scoring.py:92. KEEP. Public result arithmetic or aggregate constructor derives values from concrete attempts/counters; legitimate model contract.
  - Assert at line 97: `task('t', attempts, ['mine']).activation_score is None`.

- `test_execution_and_activation_denominators_differ`, tests/test_activation_scoring.py:100. CONDITIONAL. Merge usable=1 into same JUDGE_ERROR/PASS fixture at line 69.
  - Assert at line 107: `t.usable == 1`.
  - Assert at line 108: `t.activation_usable == 2`.

- `test_recall_counts_attempts_where_an_expected_skill_fired`, tests/test_activation_scoring.py:114. KEEP. Public result arithmetic or aggregate constructor derives values from concrete attempts/counters; legitimate model contract.
  - Assert at line 123: `mine.expected == 4`.
  - Assert at line 124: `mine.hits == 2`.
  - Assert at line 125: `mine.recall == 0.5`.

- `test_precision_counts_attempts_where_a_firing_skill_was_wanted`, tests/test_activation_scoring.py:128. KEEP. Public result arithmetic or aggregate constructor derives values from concrete attempts/counters; legitimate model contract.
  - Assert at line 143: `mine.fired == 2`.
  - Assert at line 144: `mine.hits == 1`.
  - Assert at line 145: `mine.precision == 0.5`.
  - Assert at line 146: `mine.recall == 1.0`.

- `test_per_skill_stats_ignore_unasserted_tasks`, tests/test_activation_scoring.py:149. KEEP. Public result arithmetic or aggregate constructor derives values from concrete attempts/counters; legitimate model contract.
  - Assert at line 159: `mine.expected == 1`.
  - Assert at line 160: `mine.fired == 1`.

- `test_a_skill_that_never_fires_has_no_precision`, tests/test_activation_scoring.py:163. KEEP. Public result arithmetic or aggregate constructor derives values from concrete attempts/counters; legitimate model contract.
  - Assert at line 170: `mine.precision is None`.
  - Assert at line 171: `mine.recall == 0.0`.

- `test_aggregate_averages_over_asserted_tasks_only`, tests/test_activation_scoring.py:174. KEEP. Public result arithmetic or aggregate constructor derives values from concrete attempts/counters; legitimate model contract.
  - Assert at line 181: `agg.avg_activation_score == 0.5`.
  - Assert at line 182: `agg.activation_tasks == 2`.

- `test_over_firing_divides_by_opportunities_not_by_firings`, tests/test_activation_scoring.py:188. KEEP. Public result arithmetic or aggregate constructor derives values from concrete attempts/counters; legitimate model contract.
  - Assert at line 195: `stats.precision == 0.5`.
  - Assert at line 196: `stats.unwanted_rate == 0.1`.
  - Assert at line 197: `stats.unwanted == 1`.

- `test_zero_over_firing_when_it_never_fires_unwanted`, tests/test_activation_scoring.py:200. KEEP. Public result arithmetic or aggregate constructor derives values from concrete attempts/counters; legitimate model contract.
  - Assert at line 202: `stats.unwanted_rate == 0.0`.
  - Assert at line 203: `stats.recall == 1.0`.

- `test_over_firing_is_none_when_every_attempt_wanted_it`, tests/test_activation_scoring.py:206. KEEP. Public result arithmetic or aggregate constructor derives values from concrete attempts/counters; legitimate model contract.
  - Assert at line 209: `stats.unwanted_rate is None`.

- `test_a_hijacker_has_full_recall_and_a_high_over_firing_rate`, tests/test_activation_scoring.py:212. KEEP. Public result arithmetic or aggregate constructor derives values from concrete attempts/counters; legitimate model contract.
  - Assert at line 216: `stats.recall == 1.0`.
  - Assert at line 217: `stats.unwanted_rate == pytest.approx(2 / 3)`.

- `test_aggregate_gives_every_skill_the_same_denominator`, tests/test_activation_scoring.py:220. KEEP. Public result arithmetic or aggregate constructor derives values from concrete attempts/counters; legitimate model contract.
  - Assert at line 227: `stats['a'].total == 2`.
  - Assert at line 228: `stats['b'].total == 2`.
  - Assert at line 231: `stats['a'].unwanted == 1`.
  - Assert at line 232: `stats['a'].unwanted_rate == 1.0`.

- `test_a_dormant_declared_skill_still_gets_a_row`, tests/test_activation_scoring.py:235. KEEP. Public result arithmetic or aggregate constructor derives values from concrete attempts/counters; legitimate model contract.
  - Assert at line 242: `set(stats) == {'mine', 'rival'}`.
  - Assert at line 244: `rival.expected == 0`.
  - Assert at line 245: `rival.fired == 0`.
  - Assert at line 248: `rival.recall is None`.
  - Assert at line 249: `rival.unwanted_rate == 0.0`.

- `test_rows_follow_spec_order_not_alphabetical`, tests/test_activation_scoring.py:252. KEEP. Public result arithmetic or aggregate constructor derives values from concrete attempts/counters; legitimate model contract.
  - Assert at line 255: `names == ['zebra', 'alpha']`.

- `test_an_undeclared_observed_skill_is_still_reported`, tests/test_activation_scoring.py:258. KEEP. Public result arithmetic or aggregate constructor derives values from concrete attempts/counters; legitimate model contract.
  - Assert at line 262: `names == ['mine', 'ghost']`.

- `test_headline_counts_asserted_tasks_and_says_when_some_were_lost`, tests/test_activation_scoring.py:265. KEEP. Public result arithmetic or aggregate constructor derives values from concrete attempts/counters; legitimate model contract.
  - Assert at line 275: `agg.activation_tasks == 1`.
  - Assert at line 276: `agg.activation_asserted == 2`.

- `test_opportunities_is_the_denominator_the_rate_divides_by`, tests/test_activation_scoring.py:279. CONDITIONAL. Merge opportunities=3 into same total/expected/fired/hits fixture at line 212; prefer its literal rate.
  - Assert at line 281: `stats.opportunities == 3`.
  - Assert at line 282: `stats.unwanted_rate == stats.unwanted / stats.opportunities`.

### tests/test_compare.py

- `test_identical_runs_have_zero_deltas_and_no_regression`, tests/test_compare.py:71. STRENGTHEN. all(...) is vacuous on no matched rows; assert literal matched names/scores.
  - Assert at line 77: `not comp.has_regression`.
  - Assert at line 78: `all((tc.delta == 0.0 for tc in comp.matched))`.
  - Assert at line 79: `all((not tc.regression for tc in comp.matched))`.
  - Assert at line 80: `comp.aggregate_delta == 0.0`.
  - Assert at line 81: `comp.unmatched_a == []`.
  - Assert at line 82: `comp.unmatched_b == []`.

- `test_single_task_regression_flags_only_that_task`, tests/test_compare.py:90. STRENGTHEN. Retain routing but replace sign-only delta assertions with literal rates/deltas.
  - Assert at line 97: `comp.has_regression`.
  - Assert at line 98: `not by_name['alpha'].regression`.
  - Assert at line 99: `by_name['alpha'].delta == 0.0`.
  - Assert at line 100: `by_name['beta'].regression`.
  - Assert at line 101: `by_name['beta'].delta is not None`.
  - Assert at line 102: `by_name['beta'].delta < 0`.
  - Assert at line 103: `comp.aggregate_delta < 0`.

- `test_improvement_is_not_flagged_as_regression`, tests/test_compare.py:106. STRENGTHEN. Retain routing but replace sign-only delta assertions with literal rates/deltas.
  - Assert at line 111: `not comp.has_regression`.
  - Assert at line 112: `comp.matched[0].delta is not None`.
  - Assert at line 113: `comp.matched[0].delta > 0`.

- `test_unmatched_tasks_are_reported_and_excluded_from_aggregate`, tests/test_compare.py:121. KEEP. Pure public RunComparison contract over concrete runs; warning/absence checks distinguish legitimate guard inputs.
  - Assert at line 127: `[tc.task_name for tc in comp.matched] == ['alpha']`.
  - Assert at line 128: `comp.unmatched_a == ['only_a']`.
  - Assert at line 129: `comp.unmatched_b == ['only_b']`.
  - Assert at line 131: `comp.a_matched_avg == 1.0`.
  - Assert at line 132: `comp.b_matched_avg == 1.0`.

- `test_matching_is_by_name_not_positional_id`, tests/test_compare.py:135. STRENGTHEN. Fixture ids are all task-000 despite comment; supply distinct/reordered ids and assert output order.
  - Assert at line 143: `set(by_name) == {'alpha', 'beta'}`.
  - Assert at line 144: `by_name['alpha'].delta == 0.0`.
  - Assert at line 145: `by_name['beta'].delta == 0.0`.
  - Assert at line 146: `comp.unmatched_a == []`.
  - Assert at line 147: `comp.unmatched_b == []`.

- `test_lower_raw_score_from_unusable_attempts_is_not_a_regression`, tests/test_compare.py:155. KEEP. Pure public RunComparison contract over concrete runs; warning/absence checks distinguish legitimate guard inputs.
  - Assert at line 163: `not tc.regression`.
  - Assert at line 164: `tc.b_score == success_rate(4, 4) == 1.0`.
  - Assert at line 165: `tc.a_score == success_rate(4, 5) == 0.8`.

- `test_fully_unusable_side_is_unmeasured_never_a_regression`, tests/test_compare.py:168. KEEP. Pure public RunComparison contract over concrete runs; warning/absence checks distinguish legitimate guard inputs.
  - Assert at line 175: `tc.b_score is None`.
  - Assert at line 176: `tc.delta is None`.
  - Assert at line 177: `not tc.regression`.
  - Assert at line 178: `not comp.has_regression`.
  - Assert at line 180: `comp.a_matched_avg == 0.0`.
  - Assert at line 181: `comp.b_matched_avg == 0.0`.

- `test_spec_and_k_mismatch_raise_warnings`, tests/test_compare.py:189. KEEP. Pure public RunComparison contract over concrete runs; warning/absence checks distinguish legitimate guard inputs.
  - Assert at line 195: `comp.spec_mismatch`.
  - Assert at line 196: `comp.k_mismatch`.
  - Assert at line 197: `any(('different specs' in w for w in comp.warnings))`.
  - Assert at line 198: `any(('k=5' in w and 'k=3' in w for w in comp.warnings))`.

- `test_matching_specs_and_k_produce_no_warnings`, tests/test_compare.py:201. KEEP. Pure public RunComparison contract over concrete runs; warning/absence checks distinguish legitimate guard inputs.
  - Assert at line 206: `not comp.spec_mismatch`.
  - Assert at line 207: `not comp.k_mismatch`.
  - Assert at line 208: `comp.warnings == []`.

- `test_different_judges_raise_a_warning`, tests/test_compare.py:222. KEEP. Pure public RunComparison contract over concrete runs; warning/absence checks distinguish legitimate guard inputs.
  - Assert at line 230: `comp.judge_mismatch`.
  - Assert at line 231: `any(('different judges' in w and 'claude-code:claude-opus-5-5' in w and ('claude-code:claude-sonnet-5' in w) for w in comp.warnings))`.

- `test_default_model_judges_on_different_backends_warn`, tests/test_compare.py:239. KEEP. Pure public RunComparison contract over concrete runs; warning/absence checks distinguish legitimate guard inputs.
  - Assert at line 243: `comp.judge_mismatch`.
  - Assert at line 244: `any(('codex (default model)' in w and 'pi (default model)' in w for w in comp.warnings))`.

- `test_the_same_judge_raises_no_warning`, tests/test_compare.py:250. KEEP. Pure public RunComparison contract over concrete runs; warning/absence checks distinguish legitimate guard inputs.
  - Assert at line 253: `not comp.judge_mismatch`.
  - Assert at line 254: `comp.warnings == []`.

- `test_a_judge_that_never_graded_raises_no_warning`, tests/test_compare.py:257. KEEP. Pure public RunComparison contract over concrete runs; warning/absence checks distinguish legitimate guard inputs.
  - Assert at line 262: `not diff_runs(a, b).judge_mismatch`.

- `test_verbose_comparison_renders_the_secondary_metric_columns`, tests/test_compare.py:268. STRENGTHEN. Real verbose ImportError regression test; retain path and add literal rendered row values.
  - Assert at line 280: `'pass@k' in out`.
  - Assert at line 281: `'pass^k' in out`.

- `test_the_secondary_columns_agree_with_the_task_metrics`, tests/test_compare.py:284. STRENGTHEN. Shared formula oracle never calls secondary renderer; still tests some outcome propagation, so retain/rewrite.
  - Assert at line 290: `pass_at_k(*_counts(tc.b_outcomes)) == task.pass_at_k`.
  - Assert at line 291: `pass_hat_k(*_counts(tc.b_outcomes)) == task.pass_hat_k`.

- `test_no_comparable_task_shows_no_overall_delta`, tests/test_compare.py:301. STRENGTHEN. Keep historical no-comparable phrase but check actual dash row/current pp format.
  - Assert at line 308: `'no task measured on both sides' in out`.
  - Assert at line 309: `'+0.0%' not in out`.

### tests/test_compare_cli.py

- `test_one_spec_name_twice_is_refused`, tests/test_compare_cli.py:88. KEEP. Actual Typer command and filesystem addressing path; exit/message behavior is the contract.
  - Assert at line 94: `result.exit_code == 1`.
  - Assert at line 95: `'same run' in result.output`.
  - Assert at line 96: `'2026-08-04T10-30-00Z' in result.output`.

- `test_two_spellings_of_one_file_are_refused`, tests/test_compare_cli.py:99. KEEP. Actual Typer command and filesystem addressing path; exit/message behavior is the contract.
  - Assert at line 105: `result.exit_code == 1`.
  - Assert at line 106: `'same run' in result.output`.

- `test_an_ablation_pair_addressed_by_path_compares`, tests/test_compare_cli.py:109. KEEP. Actual Typer command and filesystem addressing path; exit/message behavior is the contract.
  - Assert at line 114: `result.exit_code == 0`.
  - Assert at line 115: `'without my-skill' in result.output`.
  - Assert at line 116: `'with my-skill' in result.output`.

- `test_two_distinct_runs_of_one_spec_still_compare`, tests/test_compare_cli.py:119. KEEP. Actual Typer command and filesystem addressing path; exit/message behavior is the contract.
  - Assert at line 125: `result.exit_code == 0`.

- `test_list_names_which_run_was_the_control_arm`, tests/test_compare_cli.py:128. KEEP. Actual Typer command and filesystem addressing path; exit/message behavior is the contract.
  - Assert at line 135: `result.exit_code == 0`.
  - Assert at line 136: `'ablated' in result.output`.
  - Assert at line 137: `'my-skill' in result.output`.

- `test_an_unreadable_side_is_diagnosed_not_swallowed`, tests/test_compare_cli.py:140. KEEP. Actual Typer command and filesystem addressing path; exit/message behavior is the contract.
  - Assert at line 155: `result.exit_code == 1`.
  - Assert at line 156: `'parsing results' in result.output`.

- `test_an_unknown_format_is_refused`, tests/test_compare_cli.py:162. KEEP. Actual Typer command and filesystem addressing path; exit/message behavior is the contract.
  - Assert at line 167: `result.exit_code == 2`.
  - Assert at line 168: `"Invalid value for '--format'" in output`.

### tests/test_diagnosis.py

- `test_bad_input_exits_one`, tests/test_diagnosis.py:49. STRENGTHEN. Symbolic enum identity does not pin numeric exit despite test name; use literal numeric expected codes.
  - Assert at line 50: `diagnose(BadInput('File not found: x')).code is ExitCode.BAD_INPUT`.

- `test_cannot_run_exits_two`, tests/test_diagnosis.py:53. STRENGTHEN. Symbolic enum identity does not pin numeric exit despite test name; use literal numeric expected codes.
  - Assert at line 54: `diagnose(CannotRun('The flag is retired')).code is ExitCode.CANNOT_RUN`.

- `test_unresolvable_skills_are_bad_input`, tests/test_diagnosis.py:57. KEEP. Actual exception-to-Diagnosis mapping or rendered failure boundary. Published distinctions are meaningful.
  - Assert at line 61: `diagnosis.code is ExitCode.BAD_INPUT`.
  - Assert at line 62: `diagnosis.body == 'no such skill'`.
  - Assert at line 63: `diagnosis.title == 'Invalid skills'`.

- `test_an_ablation_error_names_ablation_not_skills`, tests/test_diagnosis.py:66. KEEP. Actual exception-to-Diagnosis mapping or rendered failure boundary. Published distinctions are meaningful.
  - Assert at line 70: `diagnosis.code is ExitCode.BAD_INPUT`.
  - Assert at line 71: `diagnosis.title == 'Invalid ablation'`.

- `test_an_invalid_spec_is_bad_input`, tests/test_diagnosis.py:74. KEEP. Actual exception-to-Diagnosis mapping or rendered failure boundary. Published distinctions are meaningful.
  - Assert at line 75: `diagnose(_validation_error()).code is ExitCode.BAD_INPUT`.

- `test_an_unreadable_run_is_bad_input`, tests/test_diagnosis.py:78. KEEP. Actual exception-to-Diagnosis mapping or rendered failure boundary. Published distinctions are meaningful.
  - Assert at line 80: `diagnose(exc).code is ExitCode.BAD_INPUT`.

- `test_a_misconfigured_backend_cannot_run`, tests/test_diagnosis.py:83. KEEP. Actual exception-to-Diagnosis mapping or rendered failure boundary. Published distinctions are meaningful.
  - Assert at line 87: `diagnosis.code is ExitCode.CANNOT_RUN`.
  - Assert at line 88: `diagnosis.title == 'Backend configuration error'`.

- `test_a_lapsed_login_cannot_run`, tests/test_diagnosis.py:91. KEEP. Actual exception-to-Diagnosis mapping or rendered failure boundary. Published distinctions are meaningful.
  - Assert at line 94: `diagnose(login).code is ExitCode.CANNOT_RUN`.
  - Assert at line 95: `diagnose(login).title == 'Not logged in'`.
  - Assert at line 96: `diagnose(RunAborted(login, _results())).title == 'Run stopped: Not logged in'`.

- `test_a_spending_cap_cannot_run`, tests/test_diagnosis.py:99. KEEP. Actual exception-to-Diagnosis mapping or rendered failure boundary. Published distinctions are meaningful.
  - Assert at line 102: `diagnosis.code is ExitCode.CANNOT_RUN`.
  - Assert at line 103: `diagnosis.title == 'Spending cap reached'`.

- `test_an_aborted_run_reports_its_cause`, tests/test_diagnosis.py:106. KEEP. Actual exception-to-Diagnosis mapping or rendered failure boundary. Published distinctions are meaningful.
  - Assert at line 111: `diagnosis.code is ExitCode.CANNOT_RUN`.
  - Assert at line 112: `diagnosis.title == 'Run stopped: Spending cap reached'`.
  - Assert at line 113: `diagnosis.body == 'out of credit'`.

- `test_an_aborted_run_distinguishes_its_two_causes`, tests/test_diagnosis.py:116. KEEP. Actual exception-to-Diagnosis mapping or rendered failure boundary. Published distinctions are meaningful.
  - Assert at line 120: `diagnose(aborted).title == 'Run stopped: Backend configuration error'`.

- `test_incomparable_runs_are_a_hard_stop_on_bad_input`, tests/test_diagnosis.py:123. KEEP. Actual exception-to-Diagnosis mapping or rendered failure boundary. Published distinctions are meaningful.
  - Assert at line 127: `diagnosis.code is ExitCode.BAD_INPUT`.
  - Assert at line 128: `diagnosis.title == 'Refusing to compare'`.

- `test_an_unknown_failure_is_not_silently_bad_input`, tests/test_diagnosis.py:131. KEEP. Actual exception-to-Diagnosis mapping or rendered failure boundary. Published distinctions are meaningful.
  - Assert at line 135: `diagnosis.code is ExitCode.CANNOT_RUN`.

- `test_a_title_makes_it_a_panel`, tests/test_diagnosis.py:138. STRENGTHEN. Tests metadata only; call render/fail to test claimed panel/one-line output.
  - Assert at line 139: `diagnose(BadInput('body', title='Validation failed')).title == 'Validation failed'`.

- `test_no_title_renders_as_one_line`, tests/test_diagnosis.py:144. STRENGTHEN. Tests metadata only; call render/fail to test claimed panel/one-line output.
  - Assert at line 145: `diagnose(BadInput('File not found: x')).title is None`.

- `test_a_body_is_shown_as_written_not_as_markup`, tests/test_diagnosis.py:149. KEEP. Actual exception-to-Diagnosis mapping or rendered failure boundary. Published distinctions are meaningful.
  - Exception contract at line 151: `pytest.raises(typer.Exit)`.
  - Assert at line 155: `'bad[/broken]' in out`.
  - Assert at line 156: `'[bold]x[/bold]' in out`.

- `test_fail_raises_typer_exit_with_the_code`, tests/test_diagnosis.py:159. KEEP. Actual exception-to-Diagnosis mapping or rendered failure boundary. Published distinctions are meaningful.
  - Exception contract at line 160: `pytest.raises(typer.Exit)`.
  - Assert at line 163: `caught.value.exit_code == ExitCode.CANNOT_RUN`.

- `test_the_reserved_bar_code_is_named`, tests/test_diagnosis.py:166. KEEP. Actual exception-to-Diagnosis mapping or rendered failure boundary. Published distinctions are meaningful.
  - Assert at line 168: `ExitCode.BAR_NOT_MET == 3`.

- `test_the_interrupt_code_is_named`, tests/test_diagnosis.py:171. SAFE DELETE. Same enum access and literal 130 are exercised by unmocked run command in tests/test_cancel.py:680.
  - Assert at line 172: `ExitCode.INTERRUPTED == 130`.

### tests/test_reporter.py

- `test_make_progress_initializes_task_totals`, tests/test_reporter.py:29. KEEP. Actual Rich render/progress state or diagnostic tuple; concrete visible output is checked.
  - Assert at line 32: `progress.tasks[task_ids['Task one']].total == 3`.
  - Assert at line 33: `progress.tasks[task_ids['Task two']].total == 3`.

- `test_update_progress_marks_early_stopped_task_finished`, tests/test_reporter.py:36. KEEP. Actual Rich render/progress state or diagnostic tuple; concrete visible output is checked.
  - Assert at line 49: `task.completed == 3`.
  - Assert at line 51: `task.fields['marks'].plain == '⊘ · ·'`.

- `test_cheat_remains_visible_when_cleanup_fails`, tests/test_reporter.py:54. KEEP. Actual Rich render/progress state or diagnostic tuple; concrete visible output is checked.
  - Assert at line 79: `'cleanup hook' in out`.
  - Assert at line 80: `'1 cheat' in out`.
  - Assert at line 81: `'⚠' in next((ln for ln in out.splitlines() if 'Cheat' in ln))`.

- `test_format_output_empty_string`, tests/test_reporter.py:176. KEEP. Actual Rich render/progress state or diagnostic tuple; concrete visible output is checked.
  - Assert at line 177: `'[no output]' in _format_output('')`.

- `test_format_output_whitespace_only`, tests/test_reporter.py:180. KEEP. Actual Rich render/progress state or diagnostic tuple; concrete visible output is checked.
  - Assert at line 181: `'[no output]' in _format_output('   \n  ')`.

- `test_format_output_short_string_unchanged`, tests/test_reporter.py:184. STRENGTHEN. Containment allows changed prefix/suffix; use literal equality.
  - Assert at line 186: `'hello world' in result`.
  - Assert at line 187: `'truncated' not in result`.

- `test_format_output_long_string_truncated`, tests/test_reporter.py:190. STRENGTHEN. Threshold-derived inputs test mechanism; homogeneous tail and >= count fail to distinguish head/excess retention.
  - Assert at line 193: `'truncated' in result`.
  - Assert at line 195: `'x' * _OUTPUT_TRUNCATE_AT in result`.

- `test_format_output_exact_limit_not_truncated`, tests/test_reporter.py:198. STRENGTHEN. Threshold-derived inputs test mechanism; homogeneous tail and >= count fail to distinguish head/excess retention.
  - Assert at line 201: `'truncated' not in result`.
  - Assert at line 202: `exact in result`.

- `test_failed_task_assert_evidence_shown_by_default`, tests/test_reporter.py:210. CONDITIONAL. Consolidate a visibility matrix with line 256 only while keeping singleton/mixed and default/verbose cases.
  - Assert at line 215: `'timeout' in out`.

- `test_failed_task_output_shown_by_default`, tests/test_reporter.py:218. CONDITIONAL. Consolidate a visibility matrix with line 256 only while keeping singleton/mixed and default/verbose cases.
  - Assert at line 223: `'the agent said this' in out`.

- `test_judge_input_shows_only_under_verbose`, tests/test_reporter.py:226. KEEP. Actual Rich render/progress state or diagnostic tuple; concrete visible output is checked.
  - Assert at line 236: `'no file' in default`.
  - Assert at line 237: `'EXPECTED' not in default`.
  - Assert at line 238: `'SCRIPTED' not in default`.
  - Assert at line 239: `'writes the EXPECTED file' in verbose`.
  - Assert at line 240: `"assert open('SCRIPTED').read()" in verbose`.
  - Assert at line 242: `saved.task_results[0].expect == 'writes the EXPECTED file'`.
  - Assert at line 243: `saved.task_results[0].attempts[0].autorater_script == "assert open('SCRIPTED').read()"`.

- `test_passing_task_detail_not_shown_by_default`, tests/test_reporter.py:248. CONDITIONAL. Consolidate a visibility matrix with line 256 only while keeping singleton/mixed and default/verbose cases.
  - Assert at line 253: `'passing output' not in out`.

- `test_only_failed_tasks_shown_in_mixed_results`, tests/test_reporter.py:256. KEEP. Actual Rich render/progress state or diagnostic tuple; concrete visible output is checked.
  - Assert at line 269: `'fail output' in out`.
  - Assert at line 270: `'file not found' in out`.
  - Assert at line 271: `'pass output' not in out`.

- `test_verbose_shows_passing_task_detail`, tests/test_reporter.py:279. CONDITIONAL. Consolidate a visibility matrix with line 256 only while keeping singleton/mixed and default/verbose cases.
  - Assert at line 284: `'passing output' in out`.

- `test_verbose_shows_all_tasks`, tests/test_reporter.py:287. CONDITIONAL. Consolidate a visibility matrix with line 256 only while keeping singleton/mixed and default/verbose cases.
  - Assert at line 295: `'pass output' in out`.
  - Assert at line 296: `'fail output' in out`.

- `test_long_output_truncated_in_rendered_output`, tests/test_reporter.py:304. STRENGTHEN. Threshold-derived inputs test mechanism; homogeneous tail and >= count fail to distinguish head/excess retention.
  - Assert at line 308: `'truncated' in out`.
  - Assert at line 310: `out.count('z') >= _OUTPUT_TRUNCATE_AT`.

- `test_empty_output_renders_no_output_marker`, tests/test_reporter.py:313. CONDITIONAL. Enabled-markup survivor at line 582 is stronger, but console configuration differs; retain mode if consolidating.
  - Assert at line 316: `'no output' in out`.

- `test_aborted_unusable_task_is_reported_as_aborted`, tests/test_reporter.py:319. KEEP. Actual Rich render/progress state or diagnostic tuple; concrete visible output is checked.
  - Assert at line 358: `'aborted' in out`.
  - Assert at line 359: `'after 1 of 3 attempts' in out`.

- `test_early_stopped_task_with_usable_pass_is_not_reported_as_aborted`, tests/test_reporter.py:362. KEEP. Actual Rich render/progress state or diagnostic tuple; concrete visible output is checked.
  - Assert at line 414: `'aborted' not in out`.
  - Assert at line 415: `'100%' in next((ln for ln in out.splitlines() if 'Task task-001' in ln))`.

- `test_autorater_reasoning_shown_for_failed_task`, tests/test_reporter.py:423. KEEP. Actual Rich render/progress state or diagnostic tuple; concrete visible output is checked.
  - Assert at line 428: `'judge said no' in out`.

- `test_update_progress_fills_the_marks_the_report_will_show`, tests/test_reporter.py:436. KEEP. Actual Rich render/progress state or diagnostic tuple; concrete visible output is checked.
  - Assert at line 443: `marks.plain == '✓ ✗ ✓ · ·'`.

- `test_update_progress_tallies_above_five_attempts`, tests/test_reporter.py:446. KEEP. Actual Rich render/progress state or diagnostic tuple; concrete visible output is checked.
  - Assert at line 453: `marks.plain == '✓6 ✗1 ·3'`.

- `test_update_progress_keeps_a_finished_trigger_probe_neutral`, tests/test_reporter.py:456. KEEP. Actual Rich render/progress state or diagnostic tuple; concrete visible output is checked.
  - Assert at line 469: `marks.plain == '— — —'`.
  - Assert at line 470: `all(('red' not in str(span.style) for span in marks.spans))`.

- `test_agent_output_that_looks_like_markup_renders_verbatim`, tests/test_reporter.py:487. KEEP. Actual Rich render/progress state or diagnostic tuple; concrete visible output is checked.
  - Assert at line 492: `'see [bold]x[/] and [/dim]' in _render_markup(results)`.

- `test_trigger_probe_attempt_shows_its_activation_verdict`, tests/test_reporter.py:495. KEEP. Actual Rich render/progress state or diagnostic tuple; concrete visible output is checked.
  - Assert at line 528: `'✗ attempt 1' in out`.
  - Assert at line 529: `'✓ attempt 2' in out`.

- `test_built_in_activations_are_shown_without_being_scored`, tests/test_reporter.py:532. KEEP. Actual Rich render/progress state or diagnostic tuple; concrete visible output is checked.
  - Assert at line 564: `'100%  2/2 ✓' in next((ln for ln in out.splitlines() if 'Overall' in ln))`.
  - Assert at line 565: `'built-in skills  claude-api 1/2  ship with claude-code · shown, not scored' in out`.

- `test_truncating_escaped_markup_cannot_expose_a_tag`, tests/test_reporter.py:571. KEEP. Actual Rich render/progress state or diagnostic tuple; concrete visible output is checked.
  - Assert at line 579: `'[/dim]' + 'y' * 10 in out`.

- `test_empty_output_marker_is_visible_when_markup_is_rendered`, tests/test_reporter.py:582. KEEP. Actual Rich render/progress state or diagnostic tuple; concrete visible output is checked.
  - Assert at line 585: `'[no output]' in _render_markup(results)`.

- `test_each_outcome_is_counted_once`, tests/test_reporter.py:588. KEEP. Actual Rich render/progress state or diagnostic tuple; concrete visible output is checked.
  - Assert at line 599: `(counts.completed, counts.successes, counts.failed) == (5, 1, 2)`.
  - Assert at line 600: `(counts.unusable, counts.unchecked, counts.usable) == (1, 1, 3)`.

- `test_a_cheat_stays_counted_after_clean_attempts`, tests/test_reporter.py:603. KEEP. Actual Rich render/progress state or diagnostic tuple; concrete visible output is checked.
  - Assert at line 606: `counts.cheated`.

- `test_a_finished_task_counts_like_its_attempts_did_live`, tests/test_reporter.py:609. STRENGTHEN. Shared OutcomeCounts constructor oracle; add literal extraction counts or actual live/final rendering.
  - Assert at line 620: `result.counts == OutcomeCounts(outcomes)`.

- `test_live_marks_sit_in_their_attempt_slot_whatever_the_finish_order`, tests/test_reporter.py:628. KEEP. Actual Rich render/progress state or diagnostic tuple; concrete visible output is checked.
  - Assert at line 642: `task.fields['marks'].plain == '· ✗ ·'`.
  - Assert at line 644: `task.completed == 1`.

- `test_per_attempt_cost_excludes_unusable_spend`, tests/test_reporter.py:660. KEEP. Actual Rich render/progress state or diagnostic tuple; concrete visible output is checked.
  - Assert at line 674: `'1K' in per_attempt`.
  - Assert at line 675: `'5K' not in per_attempt`.

- `test_a_hermes_tool_result_is_not_counted_as_a_second_call`, tests/test_reporter.py:703. KEEP. Actual Rich render/progress state or diagnostic tuple; concrete visible output is checked.
  - Assert at line 711: `_mcp_calls(results)['mail'] == (1, 1, 1)`.

- `test_a_hermes_call_is_credited_to_the_longest_matching_server`, tests/test_reporter.py:714. KEEP. Actual Rich render/progress state or diagnostic tuple; concrete visible output is checked.
  - Assert at line 723: `stats['mail_archive'] == (1, 1, 1)`.
  - Assert at line 724: `stats['mail'] == (0, 1, 0)`.

- `test_a_hook_failure_without_an_attempt_record_shows_its_output`, tests/test_reporter.py:727. KEEP. Actual Rich render/progress state or diagnostic tuple; concrete visible output is checked.
  - Assert at line 741: `'cleanup hook exited 9' in out`.
  - Assert at line 742: `'database teardown failed' in out`.
  - Assert at line 743: `"output in the task's panel" not in out`.

- `test_a_cost_from_a_zero_baseline_is_shown_absolute`, tests/test_reporter.py:746. KEEP. Actual Rich render/progress state or diagnostic tuple; concrete visible output is checked.
  - Assert at line 749: `_relative(0, 500, lambda n: _fmt_tokens(int(n))).plain == '+500'`.
  - Assert at line 750: `_relative(0, 0, _fmt_tokens).plain == '—'`.

- `test_a_server_name_containing_the_separator_is_matched_whole`, tests/test_reporter.py:753. KEEP. Actual Rich render/progress state or diagnostic tuple; concrete visible output is checked.
  - Assert at line 762: `stats['mail__archive'] == (1, 1, 1)`.
  - Assert at line 763: `stats['mail'] == (0, 1, 0)`.

- `test_a_hermes_server_with_a_leading_underscore_is_counted`, tests/test_reporter.py:766. KEEP. Actual Rich render/progress state or diagnostic tuple; concrete visible output is checked.
  - Assert at line 772: `_mcp_calls(results)['_mail'] == (1, 1, 1)`.

### tests/test_runstore.py

- `test_save_round_trips_through_load`, tests/test_runstore.py:29. STRENGTHEN. Use distinguishable payloads and verify loaded fields/content, while retaining filename/existence boundary assertions.
  - Assert at line 33: `store.load(saved).run.spec == 'my-skill'`.

- `test_save_refuses_a_spec_name_that_is_not_one_directory`, tests/test_runstore.py:37. KEEP. Real filesystem store/discovery/addressing/validation behavior. Empty/exception checks protect distinct boundary inputs.
  - Exception contract at line 39: `pytest.raises(ValueError, match='spec name')`.
  - Assert at line 42: `not (tmp_path / '.caliper').exists()`.

- `test_save_files_a_run_under_its_spec`, tests/test_runstore.py:45. KEEP. Real filesystem store/discovery/addressing/validation behavior. Empty/exception checks protect distinct boundary inputs.
  - Assert at line 49: `saved.parent == tmp_path / '.caliper' / 'results' / 'my-skill'`.

- `test_save_names_the_file_after_the_run_timestamp`, tests/test_runstore.py:52. KEEP. Real filesystem store/discovery/addressing/validation behavior. Empty/exception checks protect distinct boundary inputs.
  - Assert at line 57: `saved.stem == '2026-01-02T03-04-05Z'`.

- `test_two_runs_of_one_spec_sit_side_by_side`, tests/test_runstore.py:60. KEEP. Real filesystem store/discovery/addressing/validation behavior. Empty/exception checks protect distinct boundary inputs.
  - Assert at line 65: `store.runs('my-skill') == [first, second]`.

- `test_two_runs_in_the_same_second_both_survive`, tests/test_runstore.py:68. STRENGTHEN. Use distinguishable payloads and verify loaded fields/content, while retaining filename/existence boundary assertions.
  - Assert at line 74: `first != second`.
  - Assert at line 75: `first.exists()`.
  - Assert at line 76: `second.exists()`.
  - Assert at line 77: `second.stem == f'{first.stem}-2'`.

- `test_same_second_runs_are_listed_in_the_order_they_were_saved`, tests/test_runstore.py:80. KEEP. Real filesystem store/discovery/addressing/validation behavior. Empty/exception checks protect distinct boundary inputs.
  - Assert at line 88: `store.runs('my-skill') == [*saved, later]`.

- `test_latest_is_the_last_of_several_same_second_runs`, tests/test_runstore.py:91. KEEP. Real filesystem store/discovery/addressing/validation behavior. Empty/exception checks protect distinct boundary inputs.
  - Assert at line 96: `store.resolve('my-skill') == last`.

- `test_a_disambiguated_run_resolves_by_its_id`, tests/test_runstore.py:99. KEEP. Real filesystem store/discovery/addressing/validation behavior. Empty/exception checks protect distinct boundary inputs.
  - Assert at line 104: `store.resolve('my-skill', run=second.stem) == second`.

- `test_a_json_path_resolves_to_itself`, tests/test_runstore.py:110. KEEP. Real filesystem store/discovery/addressing/validation behavior. Empty/exception checks protect distinct boundary inputs.
  - Assert at line 114: `store.resolve(str(saved)) == saved`.

- `test_a_json_path_that_does_not_exist_resolves_to_nothing`, tests/test_runstore.py:117. KEEP. Real filesystem store/discovery/addressing/validation behavior. Empty/exception checks protect distinct boundary inputs.
  - Assert at line 118: `RunStore(tmp_path).resolve(str(tmp_path / 'gone.json')) is None`.

- `test_a_bare_spec_name_resolves_to_its_latest_run`, tests/test_runstore.py:121. KEEP. Real filesystem store/discovery/addressing/validation behavior. Empty/exception checks protect distinct boundary inputs.
  - Assert at line 128: `store.resolve('my-skill') == newest`.

- `test_a_named_run_resolves_to_that_run`, tests/test_runstore.py:131. KEEP. Real filesystem store/discovery/addressing/validation behavior. Empty/exception checks protect distinct boundary inputs.
  - Assert at line 136: `store.resolve('my-skill', run='2026-01-01T00-00-00Z') == wanted`.

- `test_a_named_run_that_is_not_there_resolves_to_nothing`, tests/test_runstore.py:139. KEEP. Real filesystem store/discovery/addressing/validation behavior. Empty/exception checks protect distinct boundary inputs.
  - Assert at line 143: `store.resolve('my-skill', run='2026-01-01T00-00-00Z') is None`.

- `test_an_unknown_spec_resolves_to_nothing`, tests/test_runstore.py:146. KEEP. Real filesystem store/discovery/addressing/validation behavior. Empty/exception checks protect distinct boundary inputs.
  - Assert at line 147: `RunStore(tmp_path).resolve('never-run') is None`.

- `test_a_spec_with_no_runs_resolves_to_nothing`, tests/test_runstore.py:150. KEEP. Real filesystem store/discovery/addressing/validation behavior. Empty/exception checks protect distinct boundary inputs.
  - Assert at line 154: `store.resolve('empty') is None`.

- `test_specs_lists_only_directories_that_hold_runs`, tests/test_runstore.py:160. KEEP. Real filesystem store/discovery/addressing/validation behavior. Empty/exception checks protect distinct boundary inputs.
  - Assert at line 167: `store.specs() == ['alpha', 'beta']`.

- `test_specs_is_empty_before_any_run`, tests/test_runstore.py:170. KEEP. Real filesystem store/discovery/addressing/validation behavior. Empty/exception checks protect distinct boundary inputs.
  - Assert at line 171: `RunStore(tmp_path).specs() == []`.

- `test_runs_is_empty_for_an_unknown_spec`, tests/test_runstore.py:174. KEEP. Real filesystem store/discovery/addressing/validation behavior. Empty/exception checks protect distinct boundary inputs.
  - Assert at line 175: `RunStore(tmp_path).runs('never-run') == []`.

- `test_load_names_the_file_it_could_not_read`, tests/test_runstore.py:181. KEEP. Real filesystem store/discovery/addressing/validation behavior. Empty/exception checks protect distinct boundary inputs.
  - Exception contract at line 192: `pytest.raises(UnreadableRun)`.
  - Assert at line 195: `str(bad) in str(caught.value)`.

- `test_load_rejects_json_that_is_not_a_run`, tests/test_runstore.py:198. KEEP. Real filesystem store/discovery/addressing/validation behavior. Empty/exception checks protect distinct boundary inputs.
  - Exception contract at line 203: `pytest.raises(UnreadableRun)`.

- `test_discover_finds_the_nearest_root`, tests/test_runstore.py:210. KEEP. Real filesystem store/discovery/addressing/validation behavior. Empty/exception checks protect distinct boundary inputs.
  - Assert at line 218: `RunStore.discover().root == package.resolve()`.

- `test_discover_walks_up_to_an_existing_root`, tests/test_runstore.py:221. KEEP. Real filesystem store/discovery/addressing/validation behavior. Empty/exception checks protect distinct boundary inputs.
  - Assert at line 228: `RunStore.discover().root == tmp_path.resolve()`.

- `test_discover_names_the_repo_root_when_no_root_exists`, tests/test_runstore.py:231. KEEP. Real filesystem store/discovery/addressing/validation behavior. Empty/exception checks protect distinct boundary inputs.
  - Assert at line 245: `store.root == tmp_path.resolve()`.
  - Assert at line 247: `not store.caliper_dir.exists()`.

- `test_discover_does_not_reach_past_the_repo_boundary`, tests/test_runstore.py:250. KEEP. Real filesystem store/discovery/addressing/validation behavior. Empty/exception checks protect distinct boundary inputs.
  - Assert at line 257: `RunStore.discover().root == repo.resolve()`.

- `test_discover_falls_back_to_the_working_directory_outside_a_repo`, tests/test_runstore.py:260. KEEP. Real filesystem store/discovery/addressing/validation behavior. Empty/exception checks protect distinct boundary inputs.
  - Assert at line 269: `RunStore.discover(work).root == work.resolve()`.

- `test_discover_accepts_an_explicit_start`, tests/test_runstore.py:272. KEEP. Real filesystem store/discovery/addressing/validation behavior. Empty/exception checks protect distinct boundary inputs.
  - Assert at line 278: `RunStore.discover(deep).root == tmp_path.resolve()`.

- `test_a_spec_path_resolves_like_its_spec_name`, tests/test_runstore.py:281. KEEP. Real filesystem store/discovery/addressing/validation behavior. Empty/exception checks protect distinct boundary inputs.
  - Assert at line 285: `store.resolve('evals/my-skill.eval.yaml') == saved`.

- `test_a_file_that_is_not_a_results_file_says_so`, tests/test_runstore.py:288. KEEP. Real filesystem store/discovery/addressing/validation behavior. Empty/exception checks protect distinct boundary inputs.
  - Assert at line 294: `store.resolve(str(other)) is None`.
  - Assert at line 295: `'is not a results file' in store.no_results(str(other))`.

- `test_a_run_holding_an_impossible_number_is_unreadable`, tests/test_runstore.py:308. KEEP. Real filesystem store/discovery/addressing/validation behavior. Empty/exception checks protect distinct boundary inputs.
  - Exception contract at line 317: `pytest.raises(UnreadableRun)`.

### tests/test_scoring.py

- `test_pass_at_k_and_pass_hat_k_are_secondary_views`, tests/test_scoring.py:30. KEEP. Actual formula or aggregate constructor over known outcomes, including legitimate None/unmeasured contracts.
  - Assert at line 32: `pass_at_k(1, 3) == 1 - (1 - 1 / 3) ** 3`.
  - Assert at line 33: `pass_hat_k(1, 3) == (1 / 3) ** 3`.
  - Assert at line 34: `pass_hat_k(3, 3) == 1.0`.
  - Assert at line 35: `pass_at_k(0, 3) == 0.0`.

- `test_no_usable_attempt_yields_no_metric_at_all`, tests/test_scoring.py:38. KEEP. Actual formula or aggregate constructor over known outcomes, including legitimate None/unmeasured contracts.
  - Assert at line 40: `success_rate(0, 0) is None`.
  - Assert at line 41: `pass_at_k(0, 0) is None`.
  - Assert at line 42: `pass_hat_k(0, 0) is None`.

- `test_score_is_the_raw_rate_over_usable_only`, tests/test_scoring.py:48. KEEP. Actual formula or aggregate constructor over known outcomes, including legitimate None/unmeasured contracts.
  - Assert at line 66: `agg.per_task[0].score == success_rate(3, 4) == 0.75`.

- `test_a_fully_unusable_task_scores_none_and_leaves_the_average_alone`, tests/test_scoring.py:69. KEEP. Actual formula or aggregate constructor over known outcomes, including legitimate None/unmeasured contracts.
  - Assert at line 79: `by_id['t1'].score is None`.
  - Assert at line 81: `agg.avg_score == by_id['t2'].score == 1.0`.
  - Assert at line 82: `agg.scored_tasks == 1`.

- `test_average_ignores_none_scores`, tests/test_scoring.py:85. CONDITIONAL. Overlap with line 69 has different noise types and a nonperfect measured rate; retain those inputs.
  - Assert at line 94: `agg.avg_score == success_rate(1, 2) == 0.5`.

- `test_every_row_records_the_runs_requested_depth`, tests/test_scoring.py:97. KEEP. Actual formula or aggregate constructor over known outcomes, including legitimate None/unmeasured contracts.
  - Assert at line 103: `agg.per_task[0].k == 5`.
  - Assert at line 104: `agg.per_task[0].successes == 1`.

- `test_no_tasks_averages_to_zero_rather_than_none`, tests/test_scoring.py:107. KEEP. Actual formula or aggregate constructor over known outcomes, including legitimate None/unmeasured contracts.
  - Assert at line 111: `agg.avg_score == 0.0`.
  - Assert at line 112: `agg.per_task == []`.

- `test_one_call_builds_both_scoreboards`, tests/test_scoring.py:146. KEEP. Actual formula or aggregate constructor over known outcomes, including legitimate None/unmeasured contracts.
  - Assert at line 153: `agg.avg_score == 0.5`.
  - Assert at line 154: `agg.avg_activation_score == 1.0`.
  - Assert at line 155: `agg.activation_asserted == 1`.
  - Assert at line 156: `[s.skill for s in agg.activation_per_skill] == ['a']`.

- `test_a_firing_description_over_a_failing_body_scores_one_and_zero`, tests/test_scoring.py:159. KEEP. Actual formula or aggregate constructor over known outcomes, including legitimate None/unmeasured contracts.
  - Assert at line 172: `agg.avg_score == 0.0`.
  - Assert at line 173: `agg.avg_activation_score == 1.0`.

- `test_a_passing_body_under_a_silent_description_scores_one_and_zero`, tests/test_scoring.py:176. KEEP. Actual formula or aggregate constructor over known outcomes, including legitimate None/unmeasured contracts.
  - Assert at line 186: `agg.avg_score == 1.0`.
  - Assert at line 187: `agg.avg_activation_score == 0.0`.

- `test_an_unasserted_run_has_no_activation_headline`, tests/test_scoring.py:190. KEEP. Actual formula or aggregate constructor over known outcomes, including legitimate None/unmeasured contracts.
  - Assert at line 196: `agg.avg_activation_score is None`.
  - Assert at line 197: `agg.activation_asserted == 0`.

- `test_a_declared_skill_gets_a_row_even_when_it_never_fired`, tests/test_scoring.py:200. KEEP. Actual formula or aggregate constructor over known outcomes, including legitimate None/unmeasured contracts.
  - Assert at line 205: `[s.skill for s in agg.activation_per_skill] == ['dormant']`.

### tests/test_skill_drift.py

- `test_identical_neighbourhoods_report_no_drift`, tests/test_skill_drift.py:69. KEEP. Actual comparison from concrete snapshots; missing or changed-source provenance has distinct semantics.
  - Assert at line 72: `diff_runs(a, b).skill_drift == []`.

- `test_a_drifted_git_source_warns`, tests/test_skill_drift.py:75. KEEP. Actual comparison from concrete snapshots; missing or changed-source provenance has distinct semantics.
  - Assert at line 81: `[d.name for d in comp.skill_drift] == ['tdd']`.
  - Assert at line 82: `comp.skill_drift[0].source_kind == 'git'`.
  - Assert at line 83: `any(('tdd' in w and 'git source' in w for w in comp.warnings))`.

- `test_a_drifted_git_source_reports_the_two_commits`, tests/test_skill_drift.py:86. CONDITIONAL. Merge both refs into identical fixture at line 75.
  - Assert at line 92: `drift.a_ref == 'a' * 7`.
  - Assert at line 93: `drift.b_ref == 'b' * 7`.

- `test_a_drifted_path_source_is_recorded_but_does_not_warn`, tests/test_skill_drift.py:96. KEEP. Actual comparison from concrete snapshots; missing or changed-source provenance has distinct semantics.
  - Assert at line 103: `[d.name for d in comp.skill_drift] == ['mine']`.
  - Assert at line 104: `comp.skill_drift[0].source_kind == 'path'`.
  - Assert at line 105: `comp.warnings == []`.

- `test_a_path_source_falls_back_to_a_content_digest`, tests/test_skill_drift.py:108. CONDITIONAL. Merge refs/length into identical fixture at line 96; strengthen digest oracle.
  - Assert at line 114: `drift.a_ref != drift.b_ref`.
  - Assert at line 115: `len(drift.a_ref) == 7`.

- `test_both_kinds_are_reported_together`, tests/test_skill_drift.py:118. KEEP. Actual comparison from concrete snapshots; missing or changed-source provenance has distinct semantics.
  - Assert at line 135: `{d.name for d in comp.skill_drift} == {'mine', 'tdd'}`.
  - Assert at line 137: `len(comp.warnings) == 1`.
  - Assert at line 138: `'tdd' in comp.warnings[0]`.

- `test_a_member_present_on_one_side_only_is_not_drift`, tests/test_skill_drift.py:141. KEEP. Actual comparison from concrete snapshots; missing or changed-source provenance has distinct semantics.
  - Assert at line 148: `comp.skill_drift == []`.
  - Assert at line 149: `comp.neighbourhood_mismatch`.

- `test_a_member_that_changed_source_kind_is_graded_as_git`, tests/test_skill_drift.py:152. STRENGTHEN. Warning truthiness can be unrelated; assert tdd and git provenance.
  - Assert at line 159: `comp.skill_drift[0].source_kind == 'git'`.
  - Assert at line 160: `comp.warnings`.

- `test_legacy_runs_without_snapshot_files_do_not_report_drift`, tests/test_skill_drift.py:163. KEEP. Actual comparison from concrete snapshots; missing or changed-source provenance has distinct semantics.
  - Assert at line 167: `diff_runs(a, b).skill_drift == []`.

### tests/test_task_metrics.py

- `test_counts_split_usable_from_noise`, tests/test_task_metrics.py:15. KEEP. Actual production derived property/model serialization or validation; input fixture is transformed before assertions.
  - Assert at line 26: `(task.successes, task.usable, task.unusable) == (3, 4, 1)`.

- `test_every_metric_shares_the_usable_denominator`, tests/test_task_metrics.py:29. KEEP. Actual production derived property/model serialization or validation; input fixture is transformed before assertions.
  - Assert at line 32: `task.score == 0.5`.
  - Assert at line 33: `task.pass_at_k == 1 - 0.5 ** 2`.
  - Assert at line 34: `task.pass_hat_k == 0.5 ** 2`.

- `test_a_cheat_is_a_usable_failure`, tests/test_task_metrics.py:37. KEEP. Actual production derived property/model serialization or validation; input fixture is transformed before assertions.
  - Assert at line 41: `(task.successes, task.usable, task.unusable) == (1, 2, 0)`.
  - Assert at line 42: `task.score == 0.5`.

- `test_a_trigger_probe_leaves_the_denominator_without_being_noise`, tests/test_task_metrics.py:45. KEEP. Actual production derived property/model serialization or validation; input fixture is transformed before assertions.
  - Assert at line 53: `(task.successes, task.usable, task.unusable) == (0, 0, 0)`.
  - Assert at line 54: `task.score is None`.

- `test_all_unusable_yields_no_metrics`, tests/test_task_metrics.py:57. KEEP. Actual production derived property/model serialization or validation; input fixture is transformed before assertions.
  - Assert at line 60: `(task.successes, task.usable, task.unusable) == (0, 0, 2)`.
  - Assert at line 61: `task.score is None`.
  - Assert at line 62: `task.pass_at_k is None`.
  - Assert at line 63: `task.pass_hat_k is None`.

- `test_a_task_with_no_attempts_has_no_metrics`, tests/test_task_metrics.py:66. KEEP. Actual production derived property/model serialization or validation; input fixture is transformed before assertions.
  - Assert at line 69: `(task.successes, task.usable, task.unusable) == (0, 0, 0)`.
  - Assert at line 70: `task.score is None`.

- `test_the_metrics_are_serialized_so_a_saved_run_reads_the_same`, tests/test_task_metrics.py:73. KEEP. Actual production derived property/model serialization or validation; input fixture is transformed before assertions.
  - Assert at line 77: `dumped['successes'] == 1`.
  - Assert at line 78: `dumped['usable'] == 2`.
  - Assert at line 79: `dumped['unusable'] == 0`.
  - Assert at line 80: `dumped['score'] == 0.5`.
  - Assert at line 81: `dumped['pass_at_k'] == 0.75`.
  - Assert at line 82: `dumped['pass_hat_k'] == 0.25`.

- `test_a_saved_run_cannot_carry_counts_that_contradict_its_attempts`, tests/test_task_metrics.py:85. KEEP. Actual production derived property/model serialization or validation; input fixture is transformed before assertions.
  - Assert at line 99: `reloaded.successes == 1`.
  - Assert at line 100: `reloaded.score == 0.5`.

- `test_a_task_with_no_execution_check_is_trigger_only`, tests/test_task_metrics.py:111. CONDITIONAL. Counterpart in test_not_checked.py has expected=[] versus this None; preserve both metadata variants.
  - Assert at line 112: `_task(Outcome.NOT_CHECKED, Outcome.NOT_CHECKED).trigger_only is True`.

- `test_trigger_only_survives_one_timeout_among_k`, tests/test_task_metrics.py:115. CONDITIONAL. Counterpart in test_not_checked.py has expected=[] versus this None; preserve both metadata variants.
  - Assert at line 120: `_task(Outcome.NOT_CHECKED, Outcome.TIMEOUT).trigger_only is True`.

- `test_a_task_with_a_real_verdict_is_not_trigger_only`, tests/test_task_metrics.py:123. SAFE DELETE. Identical PASS/NOT_CHECKED getter input with expected=None survives tests/test_not_checked.py:193.
  - Assert at line 124: `_task(Outcome.PASS, Outcome.NOT_CHECKED).trigger_only is False`.

- `test_a_task_with_no_attempts_at_all_is_not_trigger_only`, tests/test_task_metrics.py:127. KEEP. Actual production derived property/model serialization or validation; input fixture is transformed before assertions.
  - Assert at line 129: `_task().trigger_only is False`.

- `test_any_cheat_is_true_when_one_attempt_cheated`, tests/test_task_metrics.py:132. KEEP. Actual production derived property/model serialization or validation; input fixture is transformed before assertions.
  - Assert at line 133: `_task(Outcome.PASS, Outcome.CHEAT).any_cheat is True`.
  - Assert at line 134: `_task(Outcome.PASS, Outcome.TASK_FAIL).any_cheat is False`.

- `test_a_task_that_ran_short_with_nothing_measured_is_aborted`, tests/test_task_metrics.py:137. KEEP. Actual production derived property/model serialization or validation; input fixture is transformed before assertions.
  - Assert at line 138: `_task(Outcome.INFRA_ERROR).aborted(k=3) is True`.

- `test_a_task_that_ran_short_but_measured_something_is_not_aborted`, tests/test_task_metrics.py:141. KEEP. Actual production derived property/model serialization or validation; input fixture is transformed before assertions.
  - Assert at line 143: `_task(Outcome.PASS).aborted(k=3) is False`.

- `test_a_complete_task_is_never_aborted`, tests/test_task_metrics.py:146. KEEP. Actual production derived property/model serialization or validation; input fixture is transformed before assertions.
  - Assert at line 147: `_task(Outcome.INFRA_ERROR, Outcome.INFRA_ERROR).aborted(k=2) is False`.

- `test_a_trigger_probe_is_never_aborted`, tests/test_task_metrics.py:150. KEEP. Actual production derived property/model serialization or validation; input fixture is transformed before assertions.
  - Assert at line 152: `_task(Outcome.NOT_CHECKED).aborted(k=3) is False`.

- `test_a_task_rolls_up_its_own_usage`, tests/test_task_metrics.py:155. KEEP. Actual production derived property/model serialization or validation; input fixture is transformed before assertions.
  - Assert at line 158: `task.usage.attempts == 2`.
  - Assert at line 159: `task.usage.unusable_attempts == 1`.

- `test_a_legacy_run_counts_its_scored_tasks_from_its_rows`, tests/test_task_metrics.py:162. KEEP. Actual production derived property/model serialization or validation; input fixture is transformed before assertions.
  - Assert at line 178: `agg.scored_tasks == 2`.
  - Assert at line 179: `agg.measured is True`.

- `test_a_legacy_runs_unmeasured_rows_do_not_count`, tests/test_task_metrics.py:182. KEEP. Actual production derived property/model serialization or validation; input fixture is transformed before assertions.
  - Assert at line 191: `AggregateScore.model_validate(legacy).measured is False`.

- `test_a_stored_zero_stays_a_real_claim`, tests/test_task_metrics.py:194. KEEP. Actual production derived property/model serialization or validation; input fixture is transformed before assertions.
  - Assert at line 208: `AggregateScore.model_validate(current).measured is False`.

### tests/test_transcript_persistence.py

- `test_old_results_json_without_transcript_still_loads`, tests/test_transcript_persistence.py:17. CONDITIONAL. Same old AttemptRecord input as test_usage.py:258; retain transcript default as well as usage default.
  - Assert at line 25: `record.transcript is None`.
  - Assert at line 26: `record.passed is True`.

- `test_attempt_record_transcript_round_trips_through_json`, tests/test_transcript_persistence.py:29. KEEP. Actual model JSON serialization/deserialization at this nesting path; compatibility/default assertions protect schema contract.
  - Assert at line 56: `restored.transcript is not None`.
  - Assert at line 57: `len(restored.transcript) == 3`.
  - Assert at line 58: `restored.transcript[1].tool_name == 'mcp__deepwiki__read_wiki_structure'`.
  - Assert at line 59: `restored.transcript[1].tool_input == {'repo': 'edonadei/caliper'}`.
  - Assert at line 60: `restored.transcript[2].tool_output == '{"sections": ["intro"]}'`.

- `test_run_results_transcript_round_trips_through_json`, tests/test_transcript_persistence.py:63. KEEP. Actual model JSON serialization/deserialization at this nesting path; compatibility/default assertions protect schema contract.
  - Assert at line 97: `attempt.outcome == Outcome.INFRA_ERROR`.
  - Assert at line 98: `attempt.transcript is not None`.
  - Assert at line 99: `attempt.transcript[0].content == 'starting'`.

### tests/test_usage.py

- `test_total_tokens_sums_all_four_disjoint_fields`, tests/test_usage.py:52. KEEP. Actual token normalization/model aggregate/comparison/rendering path; optional usage None is meaningful boundary behavior.
  - Assert at line 59: `u.total_tokens == 128`.

- `test_total_tokens_is_none_when_nothing_reported`, tests/test_usage.py:62. KEEP. Actual token normalization/model aggregate/comparison/rendering path; optional usage None is meaningful boundary behavior.
  - Assert at line 63: `TokenUsage().total_tokens is None`.

- `test_total_tokens_treats_missing_components_as_zero`, tests/test_usage.py:66. KEEP. Actual token normalization/model aggregate/comparison/rendering path; optional usage None is meaningful boundary behavior.
  - Assert at line 67: `TokenUsage(input_tokens=10, output_tokens=5).total_tokens == 15`.

- `test_claude_usage_maps_result_event_directly`, tests/test_usage.py:75. KEEP. Actual token normalization/model aggregate/comparison/rendering path; optional usage None is meaningful boundary behavior.
  - Assert at line 94: `usage == TokenUsage(input_tokens=1000, output_tokens=200, cache_read_tokens=50, cache_creation_tokens=30)`.

- `test_claude_usage_none_without_result_event`, tests/test_usage.py:102. KEEP. Actual token normalization/model aggregate/comparison/rendering path; optional usage None is meaningful boundary behavior.
  - Assert at line 104: `ClaudeCodeHarness()._read(_proc(stdout), _ctx()).usage is None`.

- `test_codex_usage_subtracts_cached_from_input`, tests/test_usage.py:107. KEEP. Actual token normalization/model aggregate/comparison/rendering path; optional usage None is meaningful boundary behavior.
  - Assert at line 126: `usage.input_tokens == 16882 - 1920`.
  - Assert at line 127: `usage.cache_read_tokens == 1920`.
  - Assert at line 128: `usage.output_tokens == 17`.
  - Assert at line 129: `usage.cache_creation_tokens is None`.
  - Assert at line 131: `usage.total_tokens == 16882 - 1920 + 17 + 1920`.

- `test_pi_usage_sums_per_assistant_message`, tests/test_usage.py:134. KEEP. Actual token normalization/model aggregate/comparison/rendering path; optional usage None is meaningful boundary behavior.
  - Assert at line 159: `usage.input_tokens == 300`.
  - Assert at line 160: `usage.output_tokens == 30`.
  - Assert at line 161: `usage.cache_read_tokens == 5`.

- `test_hermes_usage_reads_session_totals`, tests/test_usage.py:164. KEEP. Actual token normalization/model aggregate/comparison/rendering path; optional usage None is meaningful boundary behavior.
  - Assert at line 173: `usage == TokenUsage(input_tokens=500, output_tokens=40, cache_read_tokens=12, cache_creation_tokens=8)`.

- `test_hermes_usage_none_when_totals_absent`, tests/test_usage.py:181. KEEP. Actual token normalization/model aggregate/comparison/rendering path; optional usage None is meaningful boundary behavior.
  - Assert at line 183: `HermesHarness()._read(_proc(json.dumps(record)), _ctx()).usage is None`.

- `test_codex_usage_survives_non_numeric_fields`, tests/test_usage.py:191. KEEP. Actual token normalization/model aggregate/comparison/rendering path; optional usage None is meaningful boundary behavior.
  - Assert at line 195: `CodexHarness()._read(_proc(stdout), _ctx()).usage is None`.

- `test_usage_totals_sum_all_attempts_and_break_out_unusable`, tests/test_usage.py:209. KEEP. Actual token normalization/model aggregate/comparison/rendering path; optional usage None is meaningful boundary behavior.
  - Assert at line 225: `totals.attempts == 2`.
  - Assert at line 226: `totals.wall_seconds == 40.0`.
  - Assert at line 227: `totals.input_tokens == 140`.
  - Assert at line 228: `totals.output_tokens == 10`.
  - Assert at line 229: `totals.total_tokens == 150`.
  - Assert at line 230: `totals.tokens_reported is True`.
  - Assert at line 232: `totals.unusable_attempts == 1`.
  - Assert at line 233: `totals.unusable_wall_seconds == 30.0`.
  - Assert at line 234: `totals.unusable_tokens == 40`.
  - Assert at line 236: `totals.usable_attempts == 1`.
  - Assert at line 237: `totals.usable_wall_seconds == 10.0`.

- `test_usage_totals_tokens_unreported_when_no_backend_data`, tests/test_usage.py:240. KEEP. Actual token normalization/model aggregate/comparison/rendering path; optional usage None is meaningful boundary behavior.
  - Assert at line 249: `totals.tokens_reported is False`.
  - Assert at line 250: `totals.wall_seconds == 5.0`.

- `test_old_results_json_without_usage_still_loads`, tests/test_usage.py:258. CONDITIONAL. Same old AttemptRecord input as test_transcript_persistence.py:17; retain both optional defaults.
  - Assert at line 266: `record.usage is None`.
  - Assert at line 267: `record.passed is True`.

- `test_compare_populates_usage_and_deltas`, tests/test_usage.py:303. KEEP. Actual token normalization/model aggregate/comparison/rendering path; optional usage None is meaningful boundary behavior.
  - Assert at line 308: `comp.a_usage.total_tokens == 2000`.
  - Assert at line 309: `comp.b_usage.total_tokens == 1200`.
  - Assert at line 310: `comp.token_delta == -800`.
  - Assert at line 311: `comp.wall_delta == -8.0`.
  - Assert at line 313: `comp.has_regression is False`.

- `test_compare_token_rise_is_not_a_regression`, tests/test_usage.py:316. KEEP. Actual token normalization/model aggregate/comparison/rendering path; optional usage None is meaningful boundary behavior.
  - Assert at line 320: `comp.token_delta == 800`.
  - Assert at line 321: `comp.wall_delta == 8.0`.
  - Assert at line 322: `comp.has_regression is False`.

- `test_print_results_renders_token_line`, tests/test_usage.py:330. KEEP. Actual token normalization/model aggregate/comparison/rendering path; optional usage None is meaningful boundary behavior.
  - Assert at line 335: `'cost' in out`.
  - Assert at line 336: `'tokens  2.0M in / 0 out' in out`.

- `test_print_results_renders_per_task_tokens_and_wall`, tests/test_usage.py:339. STRENGTHEN. Global strings/headings can be satisfied by footer/notes; assert scoped rows with literal values.
  - Assert at line 344: `'2.0M' in out`.
  - Assert at line 345: `'24s' in out`.

- `test_every_run_renders_a_single_report`, tests/test_usage.py:348. STRENGTHEN. Presence of Overall does not prove one report; count actual headers/Overall rows.
  - Assert at line 354: `'compare' not in out`.
  - Assert at line 356: `'Overall' in out`.

- `test_an_ablated_run_names_what_was_removed`, tests/test_usage.py:359. KEEP. Actual token normalization/model aggregate/comparison/rendering path; optional usage None is meaningful boundary behavior.
  - Assert at line 366: `'without grilling' in out`.
  - Assert at line 367: `'via --ablate' in out`.
  - Assert at line 368: `'observed, not scored' in out`.

- `test_an_ablated_run_shows_observations_on_a_passing_task`, tests/test_usage.py:371. KEEP. Actual token normalization/model aggregate/comparison/rendering path; optional usage None is meaningful boundary behavior.
  - Assert at line 396: `'observed only' in out`.
  - Assert at line 397: `'keeper' in out`.
  - Assert at line 398: `'1/1' in out`.
  - Assert at line 400: `'dormant' in out`.
  - Assert at line 401: `'0/1' in out`.

- `test_a_server_ablated_run_keeps_its_activation_verdicts`, tests/test_usage.py:404. STRENGTHEN. Fixture sets no verdict and no activation aggregate; create and assert real preserved activation score.
  - Assert at line 426: `'without mcp:weather' in out`.
  - Assert at line 427: `'via --ablate' in out`.
  - Assert at line 428: `'observed, not scored' not in out`.
  - Assert at line 429: `'observed only' not in out`.

- `test_a_normal_run_has_no_observed_activations_block`, tests/test_usage.py:432. STRENGTHEN. No observations makes absence trivial; use observed skill on non-ablated run as negative control.
  - Assert at line 437: `'observed only' not in capsys.readouterr().out`.

- `test_failure_details_still_show_why_an_attempt_failed`, tests/test_usage.py:440. KEEP. Actual token normalization/model aggregate/comparison/rendering path; optional usage None is meaningful boundary behavior.
  - Assert at line 457: `'expected 2 commits, got 1' in out`.

- `test_print_results_per_task_tokens_dash_when_unreported`, tests/test_usage.py:460. STRENGTHEN. Only asserts 3s globally; assert task-row token dash and wall.
  - Assert at line 469: `'3s' in out`.

- `test_print_comparison_renders_usage_rows`, tests/test_usage.py:472. STRENGTHEN. Global strings/headings can be satisfied by footer/notes; assert scoped rows with literal values.
  - Assert at line 477: `'Tokens' in out`.
  - Assert at line 478: `'Wall' in out`.

- `test_a_negative_token_count_is_refused`, tests/test_usage.py:485. KEEP. Actual token normalization/model aggregate/comparison/rendering path; optional usage None is meaningful boundary behavior.
  - Exception contract at line 486: `pytest.raises(ValidationError)`.

Ledger dispositions count test functions, not eliminated cases or future scenario matrices. KEEP=202, STRENGTHEN=26, CONDITIONAL=15, SAFE DELETE=2.

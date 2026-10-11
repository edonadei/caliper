# Independent audit review

ISSUES. Reviewed the audit at `e8a9302fa355653f95ff7c6d46b4d94ece9cae3c`. Reviewer model is `gpt-6.1-sol` with high reasoning, confirmed by the parent's dispatch metadata. This is a second GPT review, not a cross-provider review.

Read the substantive sections of `harness.md`, `orchestration.md`, `results.md` and `spec-skills.md`, the decision trail and investigation contract. Independently inspected the seven candidate bodies, named survivor bodies or their complete assertion and constructor expressions, shared fixture builders and relevant production branches. Inspected saved run XML, mutation logs, mutation implementations and the guard. Selectively read command records from the active workspace transcript. No tests were executed and no source or test files were edited by this reviewer. Applied the Unslop writing skill.

## Seven cuts

All seven are supportable for their current behavioral contracts. None of the named survivors is also an unconditional deletion candidate in another slice.

| Proposed removal | Required survivor | Assessment |
| --- | --- | --- |
| `test_judge.py:82`, `test_eval_judge_always_returns_eval_judge_instance` | Judge behavior tests at lines 300, 347, 381, 583 and 660 | The assertion follows direct construction of the asserted class. The constructor only stores inputs. Survivors construct the same class for all four backends and inspect actual verdict or transport behavior. Different evaluate inputs are appropriate because the removed test never evaluates. |
| `test_retry.py:389`, `test_a_cap_on_a_zero_exit_still_stops_the_run` | `test_retry.py:147`, `test_a_spending_cap_raises_instead_of_retrying` | Identical first `_result(**CAPPED)` and `NO_WAIT`, including default exit code zero. Survivor asserts the same exception plus one invocation and diagnostic wording. Its extra queue item is unreachable under the required exception. The cap-disabled mutant fails this survivor. |
| `test_retry.py:380`, `test_a_passing_attempt_that_mentions_a_throttle_is_not_respawned` | `test_retry.py:370`, `test_a_passing_attempt_that_mentions_a_cap_does_not_abort_the_run` | Identical first prose result and policy. Survivor checks one invocation, zero retries and exact output. Erroneous retry exhausts its single-item queue. The trailing clean result in the removed case adds no successful-path input. |
| `test_task_metrics.py:123`, `test_a_task_with_a_real_verdict_is_not_trigger_only` | `test_not_checked.py:193`, same name | Identical ordered PASS/NOT_CHECKED outcomes, expected=None and literal False assertion through the same property. Names and duration differ but do not participate in that property. The always-true mutant fails the survivor. Preserve the other expected=None versus expected=[] cases. |
| `test_diagnosis.py:171`, `test_the_interrupt_code_is_named` | `test_cancel.py:680`, `test_run_cli_exits_130_and_saves_an_interrupted_run` | Real command reads the unmocked enum and asserts literal exit 130, interrupted output and one saved file. The 129 mutant fails this survivor. Keep the separate write-failure interrupted-exit test and the reserved BAR_NOT_MET pin. |
| `test_codex_harness.py:562`, `test_codex_supports_mcp` | Codex customization run tests at lines 635 and 710 | Both run real CodexHarness lifecycle against process fakes and require exact customization metadata. `user_layer.stage` consumes the capability. Changing it to False fails both retained tests. Keep MCP payload rendering tests too. |
| `test_skillsnapshot.py:273`, `test_a_file_too_large_to_install_is_not_captured` | `test_skillsnapshot.py:282`, `test_the_snapshot_holds_exactly_the_files_the_install_wrote` | Both create the same six MiB blob. Survivor requires an exact installed set excluding it and equality with snapshot keys. Raising the cap to 100 MiB fails the survivor. See input qualification below. |

The snapshot pair is equivalent for the oversized-blob contract, not for every fixture input. The removed case uses default forbidden_files=None; the survivor uses ['answers/'] and a larger directory. Today `_copies` applies the same size check after `_installs` permits this blob, independently of the exclusion list. No observed separate size-filter branch is lost. Avoid claiming identical complete inputs. If exact preservation of both API argument variants becomes a requirement, parameterize the invariant before cutting the standalone case. The install-only oversized test is still conditional because it also resolves refs.

The seven-omission log records 168 passed and seven deselected. It is an affected-test selection, not a full-suite run with all seven omitted. In particular, the trigger predicate survivor is separately verified by its targeted mutation. This evidence supports the recommendations but is not a suite-wide mutation score or a timing benchmark.

## Evidence and coverage

The partition covers all 47 `tests/test_*.py` modules once, with no duplicate ownership or missing module. Counts independently recomputed from AST are 268 harness, 266 orchestration, 245 results and 135 spec/skills functions, totaling 914. Every test name in the first three slices appears in its report. The six spec/skills files have per-module dispositions and explicit stated full reading. This reviewer did not independently reread the entire suite.

XML confirms 1200 baseline cases with seven failures and two skips. Controlled XML contains 1199 cases with two skips, matching 1197 passed and one deselected. The separate tracked-eval scan passes its one test. The full fail-fast mutant XML contains 1200 cases with two skips and no failures, matching 1198 passed. Distinguish these adjusted investigation runs from an unchanged plain pytest success.

MCP no-op evidence reports 13 passed and 70 deselected. The selected negative control fails with DID NOT RAISE. This is a specific positive-observation weakness, not a claim that all MCP coverage is ineffective. Retain its distinct successful protocol inputs.

The trigger reset mutant alters only the NOT_CHECKED reset condition. The full adjusted suite remains green. The separate real runner probe obtains four outcomes normally and three under mutation for timeout, healthy NOT_CHECKED, timeout, healthy NOT_CHECKED. This is strong executed evidence of a behavioral gap in the existing regression test.

The baseline bundle-refusal test really reached an npm update. Saved output says `changed 2 packages in 9s`; source and override failures corroborate the escape. The previous installed global version was not recorded. State the side effect and unknown rollback target. Do not imply it was restored or invent a prior version.

## Qualifications for consolidation

The guard clears four CLI overrides and blocks direct subprocess commands whose first executable basename is a named agent, npm or npx. It is useful protection for these runs, not a universal external-command sandbox. Shell wrappers or differently named launchers are not comprehensively blocked. The tracked-spec option patches only root `rglob('*.eval.yaml')`, preserving unrelated traversal behavior.

The first four decision timestamps are explicitly superseded as placeholders. They support decision order only. The trail currently lacks the later MCP, fail-fast, seventh omission, interrupt mutant, final lint and synthesis events. Add those with genuine recorded times or clearly unmeasured timing rather than reconstructing exact times.

Read the consolidated `docs/qa/test-suite-audit-2026-10-10.md` in full after it appeared. Its 47 per-module rows independently match source AST function counts and baseline XML case counts; 18,762 source lines also match. No additional substantive defect was found. The parent has accepted the snapshot and guard-scope qualifications. Requested two small wording corrections: describe the seven-cut probe as verifying its selected retained tests, since the cross-module trigger survivor is checked separately; name the exact claude-code constructor survivor at test_judge.py:347 as well as the Claude alias test at :300. Replace pending review attribution after this review is delivered. The saved tracked lint result is clean; the reported repository-wide lint failure concerns pre-existing untracked `docs/qa/cli-2026-09-24/probe.py`. Do not conflate them.

Private evidence under `.git/test-suite-audit-20261010` is local and will not travel with a committed report. The consolidated report should retain the commands, exact results and evidence limits needed to assess its conclusions without those private files.

Final disposition: the seven conservative cuts are supportable. Preserve every named survivor, keep consolidation conditional, and apply the evidence-scope qualifications above. The audit should continue to report ISSUES because the host-update escape and demonstrated fail-fast regression gap require repair.

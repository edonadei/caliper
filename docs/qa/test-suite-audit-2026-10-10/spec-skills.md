# Spec and skill audit

ISSUES. Inspected at e8a9302fa355653f95ff7c6d46b4d94ece9cae3c. Method is source review of every test in six assigned modules and the production paths they exercise. Executed the global baseline, controlled run, tracked-eval scope probe, and a size-filter mutation against the proposed surviving invariant test.

| Module | Disposition | Evidence and judgment |
| --- | --- | --- |
| test_load_spec.py | Keep 30 functions; strengthen two checks and scope one repository scan. | Validation covers removed keys, missing checks, activates None versus [], pre-paid-run rejection, malformed shapes, duplicate YAML keys, blank checks, task identity and unsafe spec names. The 18 broken-spec CLI cases test separate commands, not redundant validation. |
| test_git_sources.py | Keep 37 functions; rewrite the relative-path test. | Real local Git repos exercise pinned/offline caches, short SHA versus branch names, tag peeling, concurrent cold clones, symlink boundaries and provenance. They do not call GitHub. |
| test_skills.py | Keep 33 functions; consolidate the size test if desired. | Parsing, resolution, installation, symlink exclusions and activates closure are separate contracts. No-throw positive validation tests complement explicit rejected declarations. |
| test_skillsnapshot.py | Cut one of 14 functions. | The exact install/snapshot invariant subsumes the standalone 6 MB blob exclusion. Keep content-digest edits, byte line endings, binary hashing, symlink path identity and provenance. |
| test_sandbox.py | Keep 17 functions; strengthen one assertion. | Negative controls prevent false cheat detection. Declared regex matching, absolute auto-forbidden paths, results directory shapes, recursion bounds and extensionless command keys are distinct behaviors. |
| test_doc_sync.py | Keep all four functions. | These check relations between live CLI/spec declarations and shipping documentation. They are not copies of constants. |

## Safe cut

`tests/test_skillsnapshot.py:273`, `test_a_file_too_large_to_install_is_not_captured`, creates `blob.bin` with six MiB and asserts snapshot keys are only SKILL.md. Retain `test_the_snapshot_holds_exactly_the_files_the_install_wrote` at line 282. It creates the same oversized blob, checks the exact installed set and equality with the snapshot set. The corresponding production path is `snapshot_skill` to `installed_files` to `_copies`, also used by `install_skills`. Raising `_MAX_FILE_BYTES` to 100 MiB makes the surviving invariant fail on the unexpected blob. Proof is in mutant-oversized.txt. This proves one relevant defect remains caught. It is not comprehensive mutation coverage.

`tests/test_skills.py:263`, `test_install_skips_oversized_files`, is a conditional consolidation candidate with that same invariant. Its installation assertion is subsumed, but it first resolves refs rather than constructing SkillRef directly. Preserve resolution intent if this test is folded into the invariant. Do not count this as an unconditional cut.

## Tests that need better evidence

- `tests/test_git_sources.py:422`, `test_a_local_repo_is_still_reachable_by_relative_path`, normally takes its absolute-path fallback because pytest's tmp_path is outside Path.cwd(). The baseline XML confirms normal system temp paths. In that branch this duplicates the materialization success already exercised by `test_fetch_resolves_a_branch_to_a_commit` at line 133. Rewrite with monkeypatch.chdir(tmp_path) and a literal './origin' input. Assert SHA and skill bytes. Removing it without restoring the advertised relative-path scenario would leave the coverage gap.
- `tests/test_git_sources.py:432`, `test_the_cache_key_and_messages_keep_the_spec_s_own_string`, directly checks private `_label` and one substring of `_repo_dir`. A caller-visible fallback warning and two distinct repos using the same readable slug would give stronger evidence for message identity and cache separation.
- `tests/test_load_spec.py:151`, `test_inline_assert_code_is_not_mistaken_for_a_path`, only asserts truthiness. Assert the literal complete inline script to catch truncation or substitution.
- `tests/test_load_spec.py:197`, `test_validate_rejects_the_spec`, asserts nonzero exit and absence of a success message. A crash passes these checks. Require exit 1, the expected diagnostic and no unexpected exception. Retain the cases because they protect validate's own command boundary.
- `tests/test_sandbox.py:80`, `test_an_extensionless_path_or_command_is_a_candidate`, only checks the result is nonempty. Assert the concrete offending input string for all four parameter rows.
- `tests/test_load_spec.py:280`, `test_no_repo_eval_hook_relies_on_pwd`, recursively parses untracked QA and deliberately invalid specs. Its baseline failure is caused by docs/qa/cli-2026-09-24/bad-regex.eval.yaml, not product behavior. Scope the scan to shipping eval paths or tracked files. A process-local Path.rglob substitution using git ls-files made the unchanged test pass. Proof is tracked-specs.txt.

## Why the negative tests stay

`frontmatter_name` returning None for absent or malformed names, `resolve_skills([])` returning [], no-root installation for no skills, valid activates calls returning no value and forbidden-path scans returning [] all describe actual absence contracts. Positive and rejected examples surround them. Blindly deleting tests because they contain an empty expected value would weaken the suite.

The snapshot directory-link, outside-reference, file-link and linked-root tests have similar-looking key sets but exercise different filesystem semantics. The exact invariant does not reproduce all their inputs. Keep them.

## Files read

All six test files above, tests/conftest.py, caliper/schema/spec.py, caliper/skills.py, caliper/skillfetch.py, caliper/skillsnapshot.py, caliper/sandbox.py, caliper/commands/validate.py, and relevant CLI/runner loading paths. Inspected Git history for spec and snapshot changes. No test or production edits. No paid smoke runs are required for a read-only audit.

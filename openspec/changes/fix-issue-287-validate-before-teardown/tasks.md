> Line numbers below in **group 2 onward** are as of the branch head. Line numbers in the
> issue and in `proposal.md`'s "Why" describe `origin/dev` *before* this change, which is
> what they are meant to describe. Anchors were re-derived at the final head after the last
> code commit, because a later commit on this same branch shifts them exactly as a merge
> from `dev` would.

## 1. Red tests first

TDD is mandatory here — the regression tests are as much the deliverable as the fix. Do not
touch `src/` until every test in this group has been watched failing for the right reason.

- [x] 1.1 Add `test_force_create_with_missing_grafana_secret_keeps_existing_deployment` to `tests/unit/test_cli_create_dev_smoke.py`, modelled on `test_force_create_without_docker_keeps_existing_deployment`: existing deployment dir with a marker file, `DeploymentManager.delete_deployment` patched to record calls, `create --force --services chatbot,grafana` with **no** `-e/--env-file`, asserting exit non-zero, `teardowns == []`, marker survives
- [x] 1.2 Run it and confirm it FAILS for the right reason — the teardown ran, not a fixture error. Captured: `AssertionError: existing deployment was torn down before secret validation ran`
- [x] 1.3 Add `test_force_create_with_missing_secret_keeps_existing_deployment` for a **non-grafana** secret (`grader` requires `ADMIN_PASSWORD`, absent from the `env_file` fixture). This distinguishes an ordering fix from a grafana special-case
- [x] 1.4 Add `test_force_create_with_unbuildable_compose_plan_keeps_existing_deployment`: `--force --dev` with `_discover_repo_path` patched to raise. Fails if the teardown lands above `build_compose_config` instead of below it
- [x] 1.5 Add `test_dry_force_create_reports_teardown_without_performing_it`: valid `--dry --force` exits 0, removes nothing, still prints the "[DRY RUN] Would remove existing deployment" notice
- [x] 1.6 Add `test_dry_force_create_with_missing_secret_omits_teardown_notice`: a failing dry run must **not** print the notice, because a real run would not have reached the teardown
- [x] 1.7 Add `test_force_evaluate_still_removes_existing_runtime`, guarding the helper split against breaking the benchmarking path
- [x] 1.8 Run all and confirm each fails for its own stated reason: 4 failed (the ordering defect), 3 passed as regression guards

## 2. Split the helper without changing any existing caller

- [x] 2.1 `handle_existing_deployment(base_dir, name, force)` in `src/cli/utils/helpers.py:299` keeps the non-destructive precondition and raises the existing `ClickException` verbatim. **Signature reduced to three parameters** — `dry` and `use_podman` were only ever used by the destructive branch, so keeping them would have left two dead arguments at every call site. Both callers are updated in this change, so there is no external signature to preserve
- [x] 2.2 `remove_existing_deployment(base_dir, name, force, dry, use_podman)` at `helpers.py:316-350` carries the destructive branch verbatim — the dry-run notice, and the `try/except` downgrading a failed cleanup to a warning. It takes `force` and no-ops when falsy, so callers need no guard
- [x] 2.3 `evaluate()` calls both back to back at `cli_main.py:790-791`, reproducing today's combined behaviour. It depends on the teardown having happened by its own "already exists" check just below
- [x] 2.4 Caller inventory re-derived with `grep -rn`, not recalled — which is what caught the `evaluate()` dependency the first draft of this plan got wrong

## 3. Reorder `create()`

- [x] 3.1 `handle_existing_deployment(...)` stays at `cli_main.py:171` so the no-`--force` refusal keeps today's precedence
- [x] 3.2 `remove_existing_deployment(...)` inserted at `cli_main.py:249` — after `build_compose_config` and before the `if dry:` branch. The unique position below everything that can refuse and above the dry-run early return
- [x] 3.3 Stale `# Handle existing deployment` comment replaced with one stating the invariant and why, including that `build_compose_config` can raise
- [x] 3.4 Docker preflight left where it is, deliberately above the teardown per `fix-issue-112-dry-run-docker-check`
- [x] 3.5 New tests pass

## 4. Make the failure message actionable

- [x] 4.1 `create()` catches `ValueError` from `validate_secrets` and, when no `--env-file` was supplied, re-raises naming the flag instead of leaving the operator pointed at `src/cli/managers/secrets_dummy.env`
- [x] 4.2 `src/cli/managers/secrets_manager.py` left untouched — not black-clean (~81 lines would reflow), which would fail `diff-cover --fail-under=80` for reasons unrelated to this change
- [x] 4.3 Test 1.1 asserts the message names both `GRAFANA_PG_PASSWORD` and `--env-file`

## 5. Verbosity must not change exit status

Added mid-flight: round 3 of the pre-PR adversarial review found that `create()`'s outer
handler printed the traceback at `--verbosity 4` and fell through without re-raising, so a
create that refused to proceed still exited 0. That makes this change's central promise
false in verbose mode — the deployment survives, but the caller is told it succeeded.

- [x] 5.1 Red test `test_force_create_with_missing_secret_fails_under_verbose_logging`, watched failing with `assert 0 != 0`
- [x] 5.2 Handler at `cli_main.py:307` now always raises; an existing `ClickException` is re-raised unchanged so its message and exit code survive, rather than being re-wrapped
- [x] 5.3 Whole suite re-run to confirm nothing depended on the fallthrough: 2330 passed, 1 xfailed

## 6. Verify nothing else moved

- [x] 6.1 `pytest tests/unit/test_cli_create_dev_smoke.py` → 18 passed. Every pre-existing test passes **unmodified**
- [x] 6.2 Benchmarking/evaluate unit tests run, since `evaluate()` was touched — green in the full suite
- [x] 6.3 `bash scripts/gate.sh` → 2330 passed, 1 xfailed, 3 warnings; diff coverage 93% (`helpers.py` 100%, `cli_main.py` 86.7%; the two uncovered lines are pre-existing `evaluate` bank-preflight paths caught as hunk context, not new code)
- [x] 6.4 No `Co-Authored-By` or session trailer on any commit
- [x] 6.5 **End-to-end check against the real CLI** (AGENTS.md:58), in a sandboxed `ARCHI_DIR` so no real deployment was touched. Identical command on both trees — `create --force -n e2e287 --services chatbot,grafana` with no `--env-file`:

  | tree | verbosity | exit | deployment survived |
  |---|---|---|---|
  | `origin/dev` | default | 1 | **no — directory removed** |
  | this branch | default | 1 | yes |
  | `origin/dev` | `-v 4` | **0 (reports success)** | **no — directory removed** |
  | this branch | `-v 4` | 1 | yes |

  The `-v 4` row on `origin/dev` is the sharpest form of the defect: it destroys the deployment *and* tells the caller it succeeded. Host containers verified unchanged (26 before, 26 after, identical list).
- [ ] 6.6 **Not run:** a valid forced create through to a live replacement. That requires pulling images and starting real containers on this host, and the project forbids running a non-dry `archi create` against a real deployment. The teardown code itself is unchanged — only relocated — and `test_force_create_still_tears_down_once_validation_passes` covers the success path up to the first host mutation. Recorded as a gap rather than claimed as done

## 7. Keep the documentation truthful in the same PR

- [x] 7.1 `--env-file` blockquote in `docs/docs/fasrc_archi.md` rewritten for the new ordering, with all six anchors re-derived at the final head
- [x] 7.2 The remaining warning kept accurate: `--env-file` is still required for grafana, and `--force` still leaves no running deployment if it fails *after* validation. The fix is not described as making `--force` safe
- [ ] 7.3 Note on issue #288 that its #287-dependent item is done, and drop #287 from its pending list

## 8. File what was deliberately left out

- [x] 8.1 Filed [#290](https://github.com/fasrc/archi/issues/290) — `evaluate()`'s own instance of this defect: its teardown precedes `SecretsManager` construction, so a forced evaluate with missing secrets destroys the benchmarking runtime and then fails. Deliberately preserved byte-for-byte by this change so the benchmarking path's behaviour was not altered in a PR about `create`
- [x] 8.2 Filed [#291](https://github.com/fasrc/archi/issues/291) — reformat `src/cli/managers/secrets_manager.py` to black; ~81 lines would reflow, so diff-cover fails on any behavioural edit to that file

## 9. Review loops

- [x] 9.1 Pre-PR adversarial review, 4 rounds. Round 1 (design): two wrong assumptions — `evaluate()` also calls the helper, and `build_compose_config` can refuse — plus a self-contradictory dry-run scenario. Round 2 (design): approve. Round 3 (implementation): the verbosity-4 exit-status defect. Round 4: stale docs anchors, then this artifact reconciliation
- [ ] 9.2 Open the PR against `fasrc/archi:dev` with the red-test output, the approach taken and why, and the review findings that changed the plan
- [ ] 9.3 Post-PR: request Codex review as a **comment**, then triage → fix → reply in-thread → push → re-request until a clean round or only-nits-deferred, posting an `<!-- archi-review-round:N -->` log every round
- [ ] 9.4 Merge once the re-review is clean and CI green, then `/opsx:archive fix-issue-287-validate-before-teardown`

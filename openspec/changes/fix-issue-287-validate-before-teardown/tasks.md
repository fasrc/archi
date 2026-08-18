## 1. Red tests first

TDD is mandatory here — the regression test is as much the deliverable as the fix. Do not
touch `src/` until every test in this group has been watched failing for the right reason.

- [ ] 1.1 Add `test_force_create_with_missing_grafana_secret_keeps_existing_deployment` to `tests/unit/test_cli_create_dev_smoke.py`, modelled on `test_force_create_without_docker_keeps_existing_deployment` (`:216-272`): create an existing deployment dir with a marker file, patch `DeploymentManager.delete_deployment` to record calls, invoke `create` with `--force --services chatbot,grafana` and **no** `-e/--env-file`, assert exit non-zero, `teardowns == []`, and the marker file survives
- [ ] 1.2 Run it and confirm it FAILS on `origin/dev` for the right reason — the teardown ran, not a fixture or import error. Capture the failing output verbatim for the PR body (acceptance criterion 1)
- [ ] 1.3 Add `test_force_create_with_missing_secret_keeps_existing_deployment` covering a **non-grafana** required secret missing from a supplied env file, with the same three assertions. This is the test that distinguishes an ordering fix from a grafana special-case (acceptance criterion 4)
- [ ] 1.4 Add `test_dry_force_create_reports_teardown_without_performing_it`: `--dry --force` against an existing deployment asserts exit 0, `teardowns == []`, the marker survives, and the output still contains the "[DRY RUN] Would remove existing deployment" notice. This pins the behaviour most at risk from relocating the call
- [ ] 1.5 Run all three and confirm each fails for its own stated reason before proceeding

## 2. Split the destructive half out of `handle_existing_deployment`

- [ ] 2.1 In `src/cli/utils/helpers.py`, keep `handle_existing_deployment(base_dir, name, force, dry, use_podman)` as the non-destructive precondition: when `base_dir` exists and `force` is falsy, raise the existing `ClickException` verbatim; otherwise return. Do not change the message
- [ ] 2.2 Add `remove_existing_deployment(base_dir, name, dry, use_podman)` carrying the destructive branch verbatim, including the `dry` branch that logs "[DRY RUN] Would remove existing deployment at {base_dir}" and the `try/except` that downgrades a failed cleanup to a warning
- [ ] 2.3 `grep -rn 'handle_existing_deployment' src/ tests/` and confirm `create()` is the only caller, so the split cannot affect another command
- [ ] 2.4 Confirm both functions are covered by the new tests — `helpers.py` is black-clean, so diff coverage will measure exactly the lines added

## 3. Reorder `create()`

- [ ] 3.1 In `src/cli/cli_main.py`, leave the `handle_existing_deployment(...)` call at its current position (`:164`) so the no-`--force` refusal keeps today's precedence
- [ ] 3.2 Insert `remove_existing_deployment(...)` after `config_manager.set_sources_enabled(enabled_sources)` and **above** `ServiceBuilder.build_compose_config(...)` — below all validation, above the `if dry:` early return so the dry-run notice still prints (design Decision 2)
- [ ] 3.3 Replace the now-stale `# Handle existing deployment` comment with one stating the invariant and why, in the style of the Docker preflight comment at `:140-145`, so the next person moving code has the reason and not just the ordering
- [ ] 3.4 Leave the Docker preflight at `:145-155` exactly where it is — it is deliberately above the teardown per `fix-issue-112-dry-run-docker-check`
- [ ] 3.5 Run the three new tests and confirm they now pass

## 4. Make the failure message actionable

- [ ] 4.1 In `create()`, when secret validation fails and no `--env-file` was supplied, surface a hint naming `--env-file`, rather than letting the operator be pointed at `src/cli/managers/secrets_dummy.env` inside the package (design Decision 4)
- [ ] 4.2 Do NOT edit `src/cli/managers/secrets_manager.py` — it is not black-clean (~81 lines would reflow) and editing it fails `diff-cover --fail-under=80` for reasons unrelated to this change
- [ ] 4.3 Extend test 1.1 to assert the message names both the missing secret and `--env-file` (acceptance criterion 3)

## 5. Verify nothing else moved

- [ ] 5.1 Run the whole file: `python -m pytest tests/unit/test_cli_create_dev_smoke.py -vv`. Every pre-existing test must pass **unmodified** — especially the dry-run tests (acceptance criterion 6). If one needed editing, the fix changed a contract it should not have
- [ ] 5.2 Run `bash scripts/gate.sh` bare, prefixed with `PATH=/home/austin/miniforge3/envs/archi/bin:$PATH`. Confirm black/isort clean, the suite green, and diff coverage ≥ 80% on changed lines (acceptance criterion 5)
- [ ] 5.3 Confirm no `Co-Authored-By` or session trailer on the commit

## 6. Keep the documentation truthful in the same PR

- [ ] 6.1 Update the `--env-file` blockquote in `docs/docs/fasrc_archi.md` (around `:585-605`) so it describes the new ordering. It currently states teardown precedes validation and cites `cli_main.py:164` / `:170` / `:199`; re-derive those anchors against the PR head after any merge from `dev` rather than trusting them
- [ ] 6.2 Keep the paragraph's remaining warning accurate: `--env-file` is still required for grafana, and `--force` still leaves you without a deployment if it fails *after* validation. Do not let the fix be described as making `--force` safe in general (design Non-Goals)
- [ ] 6.3 Note on issue #288 that its #287-dependent item is now done, and drop #287 from the list of pending doc updates

## 7. Review loops

- [ ] 7.1 Pre-PR: run `/codex:adversarial-review --wait` on the branch; verify each finding against the code, fix what holds (TDD), push back with reasons on what does not, commit — then re-run until a round returns zero findings or only nits. File remaining nits as tracked issues
- [ ] 7.2 Open the PR against `fasrc/archi:dev`, with the red-test output from 1.2 in the body and an explicit statement that approach (a) was taken and why (issue #287 asks for this)
- [ ] 7.3 Post-PR: request Codex review as a **comment**, then triage → fix → reply in-thread → push → re-request until a clean round or only-nits-deferred. Post an `<!-- archi-review-round:N -->` log every round, with the last naming the terminal condition
- [ ] 7.4 Merge once the re-review is clean and CI is green, then `/opsx:archive fix-issue-287-validate-before-teardown`

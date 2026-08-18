## 1. Red tests first

TDD is mandatory here — the regression tests are as much the deliverable as the fix. Do not
touch `src/` until every test in this group has been watched failing for the right reason.

- [ ] 1.1 Add `test_force_create_with_missing_grafana_secret_keeps_existing_deployment` to `tests/unit/test_cli_create_dev_smoke.py`, modelled on `test_force_create_without_docker_keeps_existing_deployment` (`:216-272`): create an existing deployment dir with a marker file, patch `DeploymentManager.delete_deployment` to record calls, invoke `create` with `--force --services chatbot,grafana` and **no** `-e/--env-file`, assert exit non-zero, `teardowns == []`, and the marker survives
- [ ] 1.2 Run it and confirm it FAILS on `origin/dev` for the right reason — the teardown ran, not a fixture or import error. Capture the failing output verbatim for the PR body (acceptance criterion 1)
- [ ] 1.3 Add `test_force_create_with_missing_secret_keeps_existing_deployment` covering a **non-grafana** required secret missing from a supplied env file, same three assertions. This distinguishes an ordering fix from a grafana special-case (acceptance criterion 4)
- [ ] 1.4 Add `test_force_create_with_unbuildable_compose_plan_keeps_existing_deployment`: invoke `--force --dev` with `_discover_repo_path` patched to raise (do **not** apply the `fake_repo_root` fixture, or patch it to raise explicitly), assert exit non-zero, `teardowns == []`, marker survives. This is the test that fails if the teardown lands above `build_compose_config` instead of below it
- [ ] 1.5 Add `test_dry_force_create_reports_teardown_without_performing_it`: `--dry --force` with valid inputs asserts exit 0, `teardowns == []`, marker survives, and the output still contains the "[DRY RUN] Would remove existing deployment" notice
- [ ] 1.6 Add `test_dry_force_create_with_missing_secret_omits_teardown_notice`: `--dry --force` with a required secret missing asserts exit non-zero, marker survives, and the notice is **absent** — a real run with those inputs would not have reached the teardown
- [ ] 1.7 Add a regression test for `archi evaluate --force` against an existing runtime directory, asserting it still removes the directory and proceeds past the "Benchmarking runtime already exists" check at `cli_main.py:752-755`. Without this, the helper split silently breaks the benchmarking path
- [ ] 1.8 Run all six and confirm each fails for its own stated reason before proceeding

## 2. Split the helper without changing any existing caller

- [ ] 2.1 In `src/cli/utils/helpers.py`, keep `handle_existing_deployment(base_dir, name, force, dry, use_podman)` as the non-destructive precondition: when `base_dir` exists and `force` is falsy, raise the existing `ClickException` verbatim; otherwise return. Do not change the message
- [ ] 2.2 Add `remove_existing_deployment(base_dir, name, force, dry, use_podman)` carrying the destructive branch verbatim — the `dry` branch logging "[DRY RUN] Would remove existing deployment at {base_dir}", and the `try/except` downgrading a failed cleanup to a warning. It takes `force` and no-ops when falsy, so callers need no guard
- [ ] 2.3 Update `evaluate()` (`src/cli/cli_main.py:748-750`) to call **both** functions back to back at its existing position, reproducing today's combined behaviour exactly. `evaluate` depends on the destructive branch — it raises at `:752-755` if the directory still exists
- [ ] 2.4 Re-run `grep -rn 'handle_existing_deployment\|remove_existing_deployment' src/ tests/` and confirm the caller inventory matches what this plan assumes. The earlier draft of this plan asserted `create()` was the only caller and was wrong; derive the list, do not recall it

## 3. Reorder `create()`

- [ ] 3.1 Leave the `handle_existing_deployment(...)` call at `:164` so the no-`--force` refusal keeps today's precedence
- [ ] 3.2 Insert `remove_existing_deployment(...)` **after** `ServiceBuilder.build_compose_config(...)` (ends `:220`) and **before** the `if dry:` branch (`:223`). This is the unique position below everything that can refuse and above the dry-run early return (design Decision 2)
- [ ] 3.3 Replace the stale `# Handle existing deployment` comment with one stating the invariant and why, in the style of the Docker preflight comment at `:140-145`, so the next person moving code has the reason and not just the ordering. State explicitly that `build_compose_config` can raise
- [ ] 3.4 Leave the Docker preflight at `:145-155` where it is — deliberately above the teardown per `fix-issue-112-dry-run-docker-check`
- [ ] 3.5 Run the six new tests and confirm they all pass

## 4. Make the failure message actionable

- [ ] 4.1 In `create()`, when secret validation fails and no `--env-file` was supplied, surface a hint naming `--env-file`, rather than letting the operator be pointed at `src/cli/managers/secrets_dummy.env` inside the package (design Decision 4)
- [ ] 4.2 Do NOT edit `src/cli/managers/secrets_manager.py` — it is not black-clean (~81 lines would reflow) and editing it fails `diff-cover --fail-under=80` for reasons unrelated to this change
- [ ] 4.3 Extend test 1.1 to assert the message names both the missing secret and `--env-file` (acceptance criterion 3)

## 5. Verify nothing else moved

- [ ] 5.1 Run the whole file: `python -m pytest tests/unit/test_cli_create_dev_smoke.py -vv`. Every pre-existing test must pass **unmodified** — especially the dry-run tests (acceptance criterion 6). If one needed editing, the fix changed a contract it should not have
- [ ] 5.2 Run the benchmarking/evaluate unit tests too, since `evaluate()` was touched
- [ ] 5.3 Run `bash scripts/gate.sh` bare, prefixed with `PATH=/home/austin/miniforge3/envs/archi/bin:$PATH`. Confirm black/isort clean, suite green, diff coverage ≥ 80% on changed lines (acceptance criterion 5)
- [ ] 5.4 Confirm no `Co-Authored-By` or session trailer on the commit

## 6. Keep the documentation truthful in the same PR

- [ ] 6.1 Update the `--env-file` blockquote in `docs/docs/fasrc_archi.md` (around `:585-605`) to describe the new ordering. It currently states teardown precedes validation and cites `cli_main.py:164` / `:170` / `:199`; re-derive those anchors against the PR head after any merge from `dev` rather than trusting them
- [ ] 6.2 Keep the remaining warning accurate: `--env-file` is still required for grafana, and `--force` still leaves you without a deployment if it fails *after* validation. Do not let the fix be described as making `--force` safe in general (design Non-Goals)
- [ ] 6.3 Note on issue #288 that its #287-dependent item is done, and drop #287 from its pending list

## 7. File what was deliberately left out

- [ ] 7.1 File a follow-up issue for `evaluate()`'s own instance of this defect: its teardown at `cli_main.py:748` precedes `SecretsManager` construction at `:757`, so a forced evaluate with missing secrets destroys the benchmarking runtime and then fails. Include the line anchors and the reason it was not folded into this PR
- [ ] 7.2 File a follow-up issue to reformat `src/cli/managers/secrets_manager.py` to black, noting it currently blocks any behavioural edit to that file via the diff-coverage gate

## 8. Review loops

- [ ] 8.1 Pre-PR: re-run `/codex:adversarial-review --wait` on the branch once the code exists; verify each finding against the code, fix what holds (TDD), push back with reasons on what does not, commit — then re-run until a round returns zero findings or only nits. File remaining nits as tracked issues
- [ ] 8.2 Open the PR against `fasrc/archi:dev`, with the red-test output from 1.2 in the body, an explicit statement that approach (a) was taken and why, and the design-review findings that changed the plan
- [ ] 8.3 Post-PR: request Codex review as a **comment**, then triage → fix → reply in-thread → push → re-request until a clean round or only-nits-deferred. Post an `<!-- archi-review-round:N -->` log every round, the last naming the terminal condition
- [ ] 8.4 Merge once the re-review is clean and CI green, then `/opsx:archive fix-issue-287-validate-before-teardown`

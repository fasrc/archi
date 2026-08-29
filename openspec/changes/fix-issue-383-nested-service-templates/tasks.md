## 1. Recurse the declared service set

Every task below ends with the whole unit suite green and one commit. Task 1.1 folds its RED
tests and their fix into a single commit on purpose: switching the traversal turns the
existing count assertion red the moment it lands, so a commit holding only the new tests, or
only the implementation, could never pass the gate.

- [x] 1.1 Recurse the traversal and re-key the exclusions, tests first, in one commit.

  **RED first, and watch it fail.** In `tests/unit/test_base_image_preflight.py`, next to
  `test_templates_missing_base_reference_reports_replaced_line` (`:1280`), add a test with a
  `tmp_path` fixture holding a digest-pinned top-level `Dockerfile-chat` (reuse `_PINNED_FROM`,
  `:1273`) and a nested `nested/Dockerfile-svc` carrying `_THIRD_PARTY_FROM` (`:1277`). Assert
  `preflight.templates_missing_base_reference(tmp_path)` reports the nested template by path.
  Run it and record the observed failure — it returns `[]` today. Put that observed failure in
  the commit message.

  **Then GREEN, in the same commit.**
  - `service_templates` (`src/cli/managers/base_image_preflight.py:87`): use
    `directory.rglob("Dockerfile*")` and compare each path against the exclusion list by
    `p.relative_to(directory).as_posix()`, not `p.name`.
  - `NON_SERVICE_TEMPLATES` (`:34`): re-key the four existing entries to relative paths (they
    are top-level, so the strings do not change) and add `base-python-image/Dockerfile` and
    `base-pytorch-image/Dockerfile`, each with a reason string in the same style as the
    existing four. Both define an a2rchi base image themselves.
  - Update the docstrings in that file that say "four" excluded or describe the keys as names.
  - `test_service_templates_has_15_of_19_and_excluded_names_match_the_declaration` (`:1260`):
    the traversal in the test becomes recursive and the excluded-set comparison becomes
    relative paths. Re-measure rather than assuming; the expected split is 21 files, 15
    services, 6 exclusions. Rename the test so its name matches the new split.

  **The service count must stay 15.** If it does not, stop and find out why — that is a
  finding, not an assertion to adjust.

  Re-run the reproduction to confirm the gap is closed: `service_templates` must now list the
  nested template and the missing-base report must name it. Then run
  `python -m pytest tests/unit/test_base_image_preflight.py tests/unit/test_python_version_declaration.py -v`,
  run the repo gate script, and commit only when it exits 0.

- [x] 1.2 Assert the refusal at the deploy entry point.

  Add the `enforce_base_images` counterpart to 1.1's helper-level test: a nested service
  template on a third-party base must make `enforce_base_images` raise
  `BaseImagePreflightError`, and the message must name that template's path. Follow the
  existing `enforce_base_images` tests in the file for how the compose config and probe are
  built.

  The entry point is the assertion that protects the operator. fasrc/archi#381 shipped a
  refusal that only lived in `required_base_images`, which has no production caller, so the
  deploy path went on silently. Green after 1.1; run the gate and commit.

- [x] 1.3 Guard the real directory and the re-keyed stale check.

  Two tests, one commit:
  - Against the **real** template directory (`preflight.TEMPLATE_DIR`, not `tmp_path`), assert
    `base-python-image/Dockerfile` and `base-pytorch-image/Dockerfile` are not in
    `service_templates()`. A temporary fixture would prove the mechanism and miss a future
    traversal change pulling the real files in.
  - Assert `stale_template_exclusions` still reports a bogus **relative-path** key, and still
    returns empty against the real directory. `stale_template_exclusions` (`:99`) already does
    `(directory / name).exists()`, which resolves a relative-path key correctly — confirm that
    rather than assume it, and change it only if the confirmation fails.

  Run the gate and commit.

## 2. Audit the downstream set

- [x] 2.1 Audit every consumer of the declaration and record what the audit found.

  Run `grep -rn "service_templates\|NON_SERVICE_TEMPLATES\|stale_template_exclusions" src/ tests/ scripts/`
  and check every hit, not only the ones already touched. Known consumers on this base:
  `tests/unit/test_base_image_preflight.py` (`:142`, `:1204`, `:1234`, `:1246`, `:1253`,
  `:1264`), `tests/unit/test_python_version_declaration.py` (`:436`, `:455`), and a comment
  reference in `scripts/dev/update_service_base_images.py` (`:485`).

  `scripts/dev/update_service_base_images.py` walks `DOCKERFILES_DIR.glob("Dockerfile*")` at
  `:320` and `:378`. **Do not narrow it to the declared set** — it is a text rewriter over
  every template including the excluded base-defining ones, so reading the declaration there
  would drop the templates it exists to rewrite. Its `glob` is non-recursive, which is a
  separate and narrower gap: a nested service template would be outside the pin rewriter's
  reach even though the preflight now refuses it. Record that at both call sites as a short
  comment naming the limitation, so the next reader does not have to re-derive it.

  Repair any consumer the audit shows is actually broken. State in the commit message which
  consumers were checked and which needed no change, and carry the same list into the PR body
  — the audit result is a deliverable even when nothing needed changing.

  Run the gate and commit.

## 3. Verify and open the PR

- [ ] 3.1 Prove the acceptance criteria and open the PR.

  Confirm each of these before opening the PR:
  - `git diff --stat origin/dev -- src/cli/templates/dockerfiles/` prints nothing. No template
    file is added, removed, moved, or edited.
  - `test_templates_missing_base_reference_on_real_directory_is_empty` still passes and the
    service-template count is still 15.
  - Each new test failed before its implementation change, and the commit message says so.
  - The repo gate script exits 0, with no `--no-verify` anywhere in the history of this branch.
  - Patch coverage against `origin/dev` is at least 80%.

  Open the PR against `fasrc/archi:dev` with `gh pr create --repo fasrc/archi --base dev`. Put
  `closes #383` in the **body**, not the title — a closing keyword in the title leaves the
  issue unlinked. Include the downstream audit result from 2.1 and the measured 21 / 15 / 6
  split. No `Co-Authored-By` trailers.

  Do not merge. A human merges.

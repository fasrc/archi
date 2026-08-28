# Tasks — declare which Dockerfile templates are service templates

Closes fasrc/archi#361.

**Read this first.** Every checkbox below must end with `bash scripts/gate.sh` green and be
committed on its own. TDD happens *inside* a checkbox: write the failing test, run it, see it
fail, implement, see it pass, commit. Do **not** split a red test and its fix across two
checkboxes — a checkbox that ends red can never be committed and the run deadlocks.

Watch every RED step fail against a **fixture** template directory built in `tmp_path`, never
by editing the real templates under `src/cli/templates/dockerfiles/`. A red real tree cannot
be committed either.

Do not edit `.github/workflows/**`, `scripts/dev/update_service_base_images.py`, or the
Dockerfile templates themselves. The 19 / 15 / 4 split is already correct.

## 1. The declared set

- [x] 1.1 **Declare the set, and guard the exclusion list's honesty.**
  In `src/cli/managers/base_image_preflight.py`, beside `PYTHON_BASE`/`PYTORCH_BASE`, add the
  exclusion list and the accessor:
  - `NON_SERVICE_TEMPLATES` — a mapping from template filename to the reason it is not a
    service template, so a reader can tell a base-defining template from a third-party-based
    one without opening it. The four entries and their reasons:
    `Dockerfile-base` and `Dockerfile-base-gpu` define the base images themselves;
    `Dockerfile-postgres` (`docker.io/pgvector/pgvector:pg17`) and `Dockerfile-grafana`
    (`docker.io/grafana/grafana-enterprise:10.2.0`) build on third-party images.
  - `service_templates(template_dir=None)` — the sorted `Path`s of every `Dockerfile*` under
    the directory whose name is not a key of `NON_SERVICE_TEMPLATES`. Default the directory to
    the module's existing `TEMPLATE_DIR` so callers need not know it.
  - `stale_template_exclusions(template_dir=None)` — the exclusion names with no file on disk.
  RED first, in `tests/unit/test_base_image_preflight.py`: against a `tmp_path` fixture
  directory, an exclusion naming a file that is not present is reported by
  `stale_template_exclusions`, and one naming a present file is not. Add a second test that
  runs `stale_template_exclusions()` against the **real** directory and asserts it is empty,
  with a failure message naming each stale entry — that is the guard that stops the list
  rotting into names that exclude nothing.
  Also assert against the real directory that `service_templates()` has 15 members out of 19
  `Dockerfile*` files, and that the 4 excluded names are exactly the keys of
  `NON_SERVICE_TEMPLATES`.

- [x] 1.2 **Fail on a set member that names no `a2rchi-*-base`, and name the file.**
  RED first, against a `tmp_path` fixture: build a directory holding one correctly pinned
  template plus one template whose `FROM` line is `docker.io/library/python:3.11`, and assert
  the new guard reports the second template's path. Deleting the base `FROM` line from a
  fixture template must turn it red — add that case too, since a removed line and a replaced
  line are different inputs.
  Then implement the guard so it reads `service_templates()` and reports every member with no
  `a2rchi-*-base` reference. Put it where the other template guards live in
  `tests/unit/test_base_image_preflight.py`, and give it a real-directory test that passes on
  the current tree. The failure message must contain the template path — a test that says only
  "a template is unpinned" costs the next reader the diagnosis.

## 2. Wire the existing guards to the declaration

- [x] 2.1 **Require a pin from every member of the set.**
  In `tests/unit/test_python_version_declaration.py`, extend
  `test_service_templates_pin_one_explicit_base_tag` (`:387`): it currently asserts the
  collected pin set is non-empty (`:395`) and internally consistent. Add the assertion that
  every member of `service_templates()` contributes at least one pin, and name the members
  that contribute none. Import the declaration from
  `src.cli.managers.base_image_preflight`; do not restate the exclusion list here.
  **Do not weaken any existing assertion in that test** — the unpinned and unnamed checks stay
  exactly as they are. Prove the new assertion discriminates with a fixture-based test beside
  it, not by touching the real templates.

- [x] 2.2 **Derive the expected count from the declaration.**
  In `tests/unit/test_base_image_preflight.py`, make the expected reference count come from
  `len(service_templates())` instead of the module constant `TEMPLATE_COUNT = 15` (`:23`).
  Three assertion sites read it (`:145`, `:1188`, `:1218`); the two later ones run against a
  release-retargeted fixture tree, so point them at the count for the directory each one
  actually reads. Keep every other assertion in
  `test_all_templates_share_one_pin_state` — the one-pin-state check and the
  `_image_map()` identity comparison are what catch a partial rewrite, and they are not
  replaced by this.
  Remove `TEMPLATE_COUNT` only once nothing reads it. If anything outside these two files
  reads it, leave it defined as `len(service_templates())` rather than deleting it, and say so
  in the commit message.

## 3. The deploy preflight

- [x] 3.1 **Refuse a service template the preflight cannot cover.**
  RED first, in `tests/unit/test_base_image_preflight.py`, against a `tmp_path` fixture
  directory: a service-set member declaring no placeable base reference makes the preflight
  refuse, and the refusal names that template. Assert the naming, not only the refusal.
  Then implement it in `required_base_images`
  (`src/cli/managers/base_image_preflight.py:92`). Follow the module's existing vocabulary —
  it already has `Outcome`, `Verdict`, and `Cause`, and this is a refusal, so express it the
  way the module expresses refusals rather than inventing a new channel. Read
  `base_image_preflight.py:9-18` before choosing; the governing invariant is stated there.
  Add the paired test that a fully covered directory returns **exactly** the references it
  returned before this change, so a correct tree's deploy behavior is provably unchanged.
  `required_base_image_names` and the two-image rule of design D4 are not modified.

- [ ] 3.2 **Check the call sites.**
  `grep -rn "required_base_images" src/ tests/` and confirm every caller handles the new
  refusal path. If a caller would now raise where it previously returned a list, that is a
  behavior change on the deploy path and it needs a test of its own. Report what you found in
  the commit message even when nothing needed changing — "no caller needed a change" is a
  result, and the next reader should not have to re-derive it.

## 4. Close out

- [ ] 4.1 **Re-derive the counts and correct the stale prose.**
  Re-run the measurement and confirm 19 / 15 / 4:
  ```bash
  ls src/cli/templates/dockerfiles/Dockerfile* | wc -l
  grep -l "a2rchi-.*-base" src/cli/templates/dockerfiles/Dockerfile* | wc -l
  grep -L "a2rchi-.*-base" src/cli/templates/dockerfiles/Dockerfile*
  ```
  If any number moved, stop and find out why before assuming the change is right.
  Correct the prose that restates the count instead of reading it:
  `tests/unit/test_python_version_declaration.py:285` and
  `tests/unit/test_base_image_preflight.py:137` both say "15". Point them at the declaration.

- [ ] 4.2 **Full gate, then the PR.**
  ```bash
  python -m pytest tests/unit/test_python_version_declaration.py \
    tests/unit/test_base_image_preflight.py \
    tests/unit/test_update_service_base_images.py -v
  bash scripts/gate.sh
  ```
  The gate must exit 0 with no `--no-verify`. New lines in
  `src/cli/managers/base_image_preflight.py` are coverage-measured
  (`scripts/gate.sh:146` measures `--cov=src`), so confirm patch coverage clears 80% on a
  clean tree before believing the gate.
  Open the PR against `fasrc/archi:dev` with `closes #361` **in the body**, not the title.
  Record in the PR body: the design decision from task 1.1 (derived-with-exclusions, and why
  the literal list was rejected), the 19 / 15 / 4 counts, the result of the task 3.2 call-site
  check, and the note that `scripts/dev/update_service_base_images.py` was deliberately left
  unwired. No `Co-Authored-By` trailers. Do not merge.

## 1. Refuse a split base-image pin

Every task below ends with the whole unit suite green and exactly one commit. Each folds its
RED test and the change that makes it pass into that single commit on purpose: the repo gate
runs `pytest tests/unit/` before every commit, so a commit holding only a failing test could
never be made, and the loop would deadlock on it.

Before each commit, format first and then stage — the pre-commit hook's `black` is a writer
while CI's is an assert, so staging first can push a misformatted file. After `git add`,
confirm `git status` reports nothing further modified.

- [ ] 1.1 Read every reference the declared set gives for a base image, tests first, in one commit.

  **RED first, and watch it fail.** In `tests/unit/test_base_image_preflight.py`, next to the
  `base_reference` tests (`:844`), add a test with a `tmp_path` fixture holding
  `Dockerfile-chat` on `ghcr.io/fasrc/a2rchi-python-base@sha256:<64 a's>` and
  `Dockerfile-piazza` on the same image at `sha256:<64 b's>`. Assert
  `preflight.base_references(preflight.PYTHON_BASE, tmp_path)` returns both references, in
  that order. Run it and record the observed failure — the name does not exist yet. Put that
  observed failure in the commit message.

  **Then GREEN, in the same commit.** In `src/cli/managers/base_image_preflight.py`:
  - Add a private `_base_reference_sources(image, template_dir)` returning a
    `Dict[str, List[Path]]` mapping each reference to the templates declaring it, built by
    walking `service_templates(template_dir)` — the declared set, not a new `glob`. Take **one
    reference per template**: the first `_FROM_BASE_RE` match in that file whose text contains
    `image`. That is `base_reference`'s existing contract, and design decision D3 explains why
    a second match in the same file is not a disagreement.
  - Add `base_references(image, template_dir) -> List[str]` returning
    `list(_base_reference_sources(...))` — the distinct references in first-seen order.
  - Re-express `base_reference` (`:123`) as the first element of `base_references`, or `None`
    when there is none. Keep its signature and its docstring's "first match" contract; add one
    sentence saying it is now the first of `base_references`, so a reader knows the guard and
    the probe read one traversal (design D1).
  - `Dict` is not yet imported from `typing` in this module (`:26`). Add it.

  **The measured behaviour of `base_reference` must not move.** Against the real template
  directory it returns `ghcr.io/fasrc/a2rchi-python-base@sha256:c068f17b8cba96682e7007c9dd5511f43fea86c796f3cbeee44e2766c5a9b8e8`
  for the python base and the `c29c6e8b…` digest for the pytorch base. `test_base_references_are_pinned_to_the_expected_digests`
  (`:74`, reading at `:86-87`) reads exactly this. If either moves, stop — that is a finding, not an assertion
  to adjust.

  Run `python -m pytest tests/unit/test_base_image_preflight.py tests/unit/test_python_version_declaration.py -v`,
  then the repo gate script, and commit only when it exits 0.

- [ ] 1.2 Refuse the disagreement from `required_base_images`.

  **RED first.** Add a test using the same two-digest fixture asserting
  `preflight.required_base_images(gpu_ids=None, grader_enabled=False, template_dir=tmp_path)`
  raises `preflight.BaseImagePreflightError`, and that `str(exc_info.value)` contains
  `Dockerfile-chat`, `Dockerfile-piazza`, and **both** digest strings. Watch it fail: today it
  returns a one-element list naming only the `aaaa…` digest. Record that in the commit message.

  **Then GREEN.**
  - Add `_refuse_divergent_base_references(names, template_dir)` beside
    `_refuse_uncoverable_templates` (`:521`). For each image in `names`, read
    `_base_reference_sources`; when it holds more than one reference, raise
    `BaseImagePreflightError` naming the image, each reference, and under each reference the
    templates that declare it. Follow the message shape of `_refuse_uncoverable_templates`:
    a `Base image check failed: …` lead line, then indented detail lines.
  - Its docstring states the scope decision (design D2) explicitly: agreement is checked only
    for the images `required_base_image_names` returns, so a split pin on a base this
    deployment will not build is not refused; and it says what that costs — the split stays
    hidden until a deployment that does need that base.
  - In `required_base_images` (`:138`), derive `names = required_base_image_names(gpu_ids,
    grader_enabled)` before the loop, call `_refuse_divergent_base_references(names,
    template_dir)` directly after the existing `_refuse_uncoverable_templates(template_dir)`
    call (`:158`), and iterate `names`.

  Run the gate and commit.

- [ ] 1.3 Refuse it at the entry point `archi create` actually calls, before any image work.

  **RED first.** Add the `enforce_base_images` counterpart, modelled on
  `test_enforce_base_images_refuses_an_uncoverable_service_template` (`:1373`): the same
  two-digest fixture, a `FakeProbe` (`:543`), assert `BaseImagePreflightError` is raised, that
  the message names both templates, and — the load-bearing assertion — that
  `probe.pulled == []`. Watch it fail: today the call returns outcomes and the probe has pulled.
  Record that in the commit message.

  **Then GREEN.** In `enforce_base_images` (`:537`), call
  `_refuse_divergent_base_references(names, template_dir)` directly after `names` is derived
  (`:576-578`) and before the reference loop (`:580`).

  Placement is the whole point. After `names` because the check is scoped to what the
  deployment requires; before the loop so it precedes `run_preflight` (`:602`) and therefore
  `remove_existing_deployment()` (`cli_main.py:294`). Extend the comment already at `:570-573`,
  which explains the same ordering for the uncoverable-template refusal, rather than writing a
  second comment saying the same thing.

  Also add the dry-run counterpart, following
  `test_enforce_base_images_refuses_an_uncoverable_template_on_a_dry_run_too` (`:1400`): a dry
  run that reports nothing wrong about a split pin is the same lie.

  Run the gate and commit.

- [ ] 1.4 Pin the boundaries: scope, the agreeing case, and the real directory.

  Four tests, one commit. These should be green the moment they are written; if one is not,
  the implementation disagrees with the design and that is a finding.

  - **Scope, negative.** A directory whose python base agrees everywhere but whose pytorch base
    is declared at two references returns the python reference without raising for
    `gpu_ids=None, grader_enabled=False`.
  - **Scope, positive.** The same directory raises for `grader_enabled=True`, and the message
    names `a2rchi-pytorch-base`. Together these two are the docstring's claim, held by a test.
  - **The agreeing case is unchanged.** A directory where every template agrees returns exactly
    the references it returns today, for both `gpu_ids=None, grader_enabled=False` and
    `gpu_ids="all", grader_enabled=True`. Model the fixture on
    `test_required_base_images_returns_unchanged_references_when_all_templates_covered`
    (`:1346`), which already asserts both selections.
  - **One template, two `FROM` lines, no disagreement.** A single template naming the same base
    twice at different references, with every other template agreeing with its first line, is
    not refused. This is design D3: it keeps PR #387's multistage work from turning into a
    regression the moment it merges.

  Then assert against the **real** template directory (`preflight.TEMPLATE_DIR`, not
  `tmp_path`) that `base_references` returns exactly one reference for each of
  `PYTHON_BASE` and `PYTORCH_BASE`. A temporary fixture proves the mechanism; only the real
  directory catches a future template landing on a second digest.

  Run the gate and commit.

- [ ] 1.5 Document the new refusal.

  In `docs/docs/developer_guide.md`, the paragraph at `:538-544` — "Its bound is worth
  knowing…" — is where the guide says what the deploy preflight refuses about the declared
  service set. Add the new case there: the preflight also refuses when two service templates
  name the same required base image at different references, because it can probe only one of
  them, and a split pin would otherwise be established on one digest and built on another.
  Say that the check covers only the bases the deployment requires, and name
  `test_service_templates_pin_one_explicit_base_tag` as the repository-side guard that the
  new check does not replace — it keys on the annotation, not on the reference.

  **Check for a heading first.** If the branch has been rebased and a
  `### Which service templates the deploy preflight refuses` section now exists (PR #387 adds
  one), put the case in that numbered list instead, in that section's voice, and leave the
  paragraph above alone. Do not create the heading yourself if it is absent.

  A docs-only commit has no lines under `src/`, so patch coverage reports nothing to measure
  and the gate passes on formatting and the suite alone. Run the gate and commit.

## 2. Verify and open the PR

- [ ] 2.1 Prove the acceptance criteria and open the PR.

  Confirm each of these before opening the PR:
  - The reproduction from the issue now raises `BaseImagePreflightError` instead of printing a
    `required:` line, and the message names `Dockerfile-chat`, `Dockerfile-piazza`, and both
    digests. Run it by piping the script into `python` from the branch checkout, so it imports
    the branch's module and not an installed copy; print `preflight.__file__` to prove which.
  - `enforce_base_images` raises on the same fixture with `probe.pulled == []`.
  - `git diff --stat origin/dev -- src/cli/templates/dockerfiles/` prints nothing.
  - `base_reference` against the real directory still returns the `c068f17b…` and `c29c6e8b…`
    digests, and the service-template count is still 15.
  - `python -m pytest tests/unit/test_base_image_preflight.py -q` and
    `python -m pytest tests/unit/test_python_version_declaration.py -q` are both green.
  - Each new test failed before its implementation change, and the commit message says so.
  - The repo gate script exits 0, with no `--no-verify` anywhere in this branch's history, and
    patch coverage against `origin/dev` is at least 80%.

  Push with `git push -u origin fix/issue-389-divergent-base-pins`. The branch was created with
  `checkout -b` from `origin/dev`, so its upstream is the trunk until `-u` repoints it.

  Open the PR against `fasrc/archi:dev` with `gh pr create --repo fasrc/archi --base dev`. Put
  `closes #389` in the **body**, not the title — a closing keyword in the title leaves the
  issue unlinked. In the body, state the measured before/after of the reproduction, and note
  that PRs #387 and #388 touch the same module and will conflict textually with whichever
  merges second. No `Co-Authored-By` trailers.

  Confirm the PR was opened on `fasrc/archi` and not on a fork, and confirm the pushed head
  SHA matches local `HEAD`.

  Do not merge. A human merges.

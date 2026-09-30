# Tasks — pin the 15 service templates to ghcr digests

Every checkbox below is one loop turn and ends **green and committed**. Write the failing
test, watch it fail, make the smallest change that passes it, run `bash scripts/gate.sh`,
commit. Never end a task with the suite red, and never use `--no-verify`.

Four standing notes for every task:

- **Coverage.** The diff is Dockerfile templates plus a test file. `scripts/gate.sh:146`
  measures `--cov=src` over Python only, so `diff-cover` reports "no lines with coverage
  information" and the 80% patch gate passes on that. Do not chase a coverage number here.
- **Format before staging.** The pre-commit hook's black is a writer and runs after
  `git add`, while CI's black is an assert. Run the formatter, then stage, then commit, and
  confirm `git status` is empty afterwards.
- **No `openspec` in the sandbox.** Do not add a validation step that calls it; the binary is
  not there and the task would halt the loop instead of failing.
- **Scope.** Do not touch any workflow file, `deploy/**`,
  `scripts/dev/update_service_base_images.py`, or
  `src/cli/managers/base_image_preflight.py`. All three are already digest-ready; see
  `design.md`.

## 1. Pin the templates

- [x] 1.1 `model: opus` — RED first, then the rewrite, in this one turn, because the test
      reads the real templates and cannot go green until they change.
      **RED:** in `tests/unit/test_base_image_preflight.py`, add a test asserting that
      `preflight.base_reference(preflight.PYTHON_BASE)` returns a reference matching
      `@sha256:[0-9a-f]{64}`, and the same for `preflight.PYTORCH_BASE`. Assert the exact
      digests too: python is
      `sha256:c068f17b8cba96682e7007c9dd5511f43fea86c796f3cbeee44e2766c5a9b8e8`, pytorch is
      `sha256:c29c6e8b4262736e3a5d3d47756b0d483db88254a91b16932d37f498bc704b5e`. Follow the
      file's existing real-template pattern — `test_required_references_carry_the_pin_from_the_templates`
      (`tests/unit/test_base_image_preflight.py:58`) globs the real `TEMPLATE_DIR`; do the
      same rather than building a fixture. Run it and watch it fail: today those references
      end in `:dev-4314ac4`.
      **GREEN:** rewrite the 15 templates with the script, not by hand:
      ```bash
      python scripts/dev/update_service_base_images.py \
        --switch-source ghcr --orig-tag all --tag dev-4314ac4 \
        --digest python=sha256:c068f17b8cba96682e7007c9dd5511f43fea86c796f3cbeee44e2766c5a9b8e8 \
        --digest pytorch=sha256:c29c6e8b4262736e3a5d3d47756b0d483db88254a91b16932d37f498bc704b5e
      ```
      It must print `Updated …` for 15 files. `--orig-tag all` is required — the default is
      `latest`, which matches nothing here and would exit 0 having written nothing.
      Gate green; commit.

- [x] 1.2 `model: sonnet` — Guard the shape, so a later commit cannot quietly put a tag back.
      **These pass the moment 1.1 lands — that is the point of them. Do not contrive a
      failure first.** In `tests/unit/test_base_image_preflight.py`, add tests over the real
      `TEMPLATE_DIR` asserting all four:
      1. No `FROM` line matches a tag-shaped ghcr reference
         (`^FROM ghcr\.io/fasrc/[^@]+:[^@\s]+\s*$`).
      2. Exactly 15 files carry a line matching
         `^FROM ghcr\.io/fasrc/[a-z0-9-]+@sha256:[0-9a-f]{64}`.
      3. Every digest-pinned `FROM` line has
         `# base-image-pin: dev-4314ac4 (managed by update_service_base_images.py)` as the
         line directly above it.
      4. No `FROM` line anywhere in the directory contains a `#`. This one is the
         regression guard for the defect review caught on #342: a `#` on a `FROM` line makes
         every service build fail at parse time.
      Each assertion needs a non-vacuity check — assert the collection it scanned is
      non-empty before asserting nothing offends, matching the idiom the file already uses.
      Gate green; commit.

## 2. Close out

- [x] 2.1 `model: sonnet` — Run `bash scripts/gate.sh` once more on the finished change and
      confirm it exits 0. Confirm `git status --porcelain` is empty after the last commit.
      Push with `git push -u origin fix/issue-335-pin-service-dockerfiles-to-digests` — the
      branch tracks `origin/dev`, so `-u` is required or the push retargets the trunk.
      Open the pull request with
      `gh pr create --repo fasrc/archi --base dev`, and put `closes #335` in the **body**;
      a closing keyword in the title does not link the issue. In the body, state the one
      deviation from the issue plainly: the annotation is
      `# base-image-pin: dev-4314ac4 (managed by update_service_base_images.py)`, not the
      `# base image: dev-4314ac4` the issue's text asks for, because that is what the #334
      script writes and what `test_service_templates_pin_one_explicit_base_tag` requires —
      the reasoning is in `proposal.md`. Also record that the parse check the issue asks for
      was run in Loop 1 rather than in the loop, because the sandbox has no container
      runtime: `docker build --check` on `Dockerfile-chat` exited 0 with no warnings, and
      `podman build --pull=never` parsed the `FROM` and failed only on the image's local
      absence. Stop there. **Do not merge.**

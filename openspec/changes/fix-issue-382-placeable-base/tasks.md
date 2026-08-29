# Tasks — require a service template's base to be one the preflight can probe

Closes fasrc/archi#382.

**Read this first.** Every checkbox below must end with `bash scripts/gate.sh` green and be
committed on its own. TDD happens *inside* a checkbox: write the failing test, run it, watch it
fail, implement, watch it pass, commit. Do **not** split a red test and its fix across two
checkboxes — a checkbox that ends red can never be committed and the run deadlocks.

Every RED step runs against a `tmp_path` fixture directory. Never edit the real templates under
`src/cli/templates/dockerfiles/` to make something fail — a red real tree cannot be committed
either, and the fixture proves the same discrimination.

Do not edit `_FROM_BASE_RE`, the Dockerfile templates, `.github/workflows/**`, or
`scripts/dev/update_service_base_images.py`. This is one function's strictness.

Branch base is `origin/dev`: PR #380 merged on 2026-08-28 (`acce8598`), so `service_templates`,
`templates_missing_base_reference`, and `_refuse_uncoverable_templates` are all present on `dev`.
Verified at `7c9915d0`.

## 0. Re-derive the state before changing anything

- [x] 0.1 **Confirm both gaps are live on the branch base, and that no fixture already trips.**
  This checkbox ends with **no source change** to `src/`, so commit it as the change artifacts
  themselves (this file, `proposal.md`, `specs/`) if they are not yet committed; otherwise fold
  the measurements into task 1.1's commit message rather than leaving an empty commit.
  ```bash
  git log --oneline -1
  grep -n "_FROM_BASE_RE\|^PYTHON_BASE\|^PYTORCH_BASE" src/cli/managers/base_image_preflight.py
  sed -n '109,125p' src/cli/managers/base_image_preflight.py
  grep -ho 'a2rchi-[a-z]*-base' src/cli/templates/dockerfiles/Dockerfile* | sort -u
  for f in src/cli/templates/dockerfiles/Dockerfile*; do n=$(grep -c '^FROM' "$f"); \
    [ "$n" -gt 1 ] && echo "$f: $n FROM lines"; done
  ```
  Expected: only `a2rchi-python-base` and `a2rchi-pytorch-base`, and no multistage template.
  If either moved, **stop** — a template changed under you and that is the more interesting news.

  Then run the reproduction. Pipe via stdin so the import resolves to this checkout rather than
  an installed copy, and print `pf.__file__` to prove which module you measured:
  ```bash
  python - <<'EOF'
  import pathlib, sys, tempfile
  sys.path.insert(0, ".")
  from src.cli.managers import base_image_preflight as pf
  print("module:", pf.__file__)

  PINNED = "FROM ghcr.io/fasrc/a2rchi-python-base@sha256:" + "a"*64 + "\n"

  class Probe:
      container_tool = "docker"
      def runtime_available(self): return True
      def image_present(self, ref): return True
      def pull(self, ref): return None
      def python_version(self, ref): return "Python 3.11.9"
      def reachable(self, ref): return None

  class Plan:
      gpu_ids = None
      def get_service(self, name): raise ValueError(name)

  def case(label, files):
      d = pathlib.Path(tempfile.mkdtemp())
      for n, t in files.items():
          (d / n).write_text(t)
      print(f"\n[{label}]")
      print("  missing:", [p.name for p in pf.templates_missing_base_reference(d)])
      try:
          out = pf.enforce_base_images(Plan(), probe=Probe(), template_dir=d)
          print("  enforce: NO REFUSAL ->", [o.reference for o in out])
      except pf.BaseImagePreflightError as e:
          print("  enforce: REFUSED:", str(e).splitlines()[0])

  case("unknown a2rchi base", {
      "Dockerfile-chat": PINNED,
      "Dockerfile-node": "FROM ghcr.io/fasrc/a2rchi-node-base@sha256:" + "b"*64 + "\n",
  })
  case("multistage, final stage third-party", {
      "Dockerfile-chat": PINNED,
      "Dockerfile-multi": (
          "FROM ghcr.io/fasrc/a2rchi-python-base@sha256:" + "c"*64 + " AS builder\n"
          "RUN pip wheel .\n"
          "FROM docker.io/library/debian:12\n"
          "COPY --from=builder /wheels /wheels\n"
      ),
  })
  EOF
  ```
  Both cases must print `missing: []` and `enforce: NO REFUSAL`. Keep this script — task 3.1
  re-runs it and both cases must then print the template and `enforce: REFUSED`.

  Read the two `if` statements at `tests/unit/test_base_image_preflight.py:277-281` and confirm
  for yourself that neither catches an unknown base name. Do not take this file's word for it.

## 1. Finding 1 — an a2rchi base the preflight never probes

- [x] 1.1 **Name the placeable set, and report a template outside it. RED first, both levels.**
  In `tests/unit/test_base_image_preflight.py`, beside
  `test_templates_missing_base_reference_reports_replaced_line` (`:1291`), add **two** tests
  against a `tmp_path` fixture holding a digest-pinned `Dockerfile-chat` (reuse `_PINNED_FROM`,
  `:1284`) plus `Dockerfile-node` on
  `FROM ghcr.io/fasrc/a2rchi-node-base@sha256:<64 hex>`:
  1. `templates_missing_base_reference` reports `Dockerfile-node`.
  2. `enforce_base_images` raises `BaseImagePreflightError` naming `Dockerfile-node`. Model it on
     `test_enforce_base_images_refuses_an_uncoverable_service_template` (`:1373`).

  The second is the assertion that matters — `enforce_base_images` is what `src/cli/cli_main.py:282`
  calls, and a guard reachable only from `required_base_images` protects nobody (fasrc/archi#381).
  Run both and watch them fail: today the check returns `[]` and the preflight returns a
  complete-looking list.

  Then implement, in `src/cli/managers/base_image_preflight.py`:
  - Add a module constant beside `PYTHON_BASE` / `PYTORCH_BASE` (`:26-27`) naming the a2rchi
    bases the preflight can probe, with a comment saying why the set is named once: the coverage
    check and `required_base_image_names` must not be able to disagree about which bases exist.
  - Make `required_base_image_names` (`:168`) **derive its answer from that constant** rather than
    re-listing the two names. Its behaviour must not change: the python base always, the pytorch
    base exactly when `gpu_ids` is truthy or `grader_enabled` is true. Design D4 is untouched.
  - Rewrite the comprehension at `:115-119` to collect every `_FROM_BASE_RE` match with
    `finditer` and treat the template as covered only when a match names a base in the placeable
    set. A template with no match at all is still reported, as today.

  Leave the multistage gap open — task 1.2 owns it, and its RED depends on this task not having
  closed it. Do **not** touch `_FROM_BASE_RE`.

  Gate green, then commit. State the observed pre-change failure in the commit message.

## 2. Finding 3 — the stage the deployment actually runs

- [ ] 2.1 **Judge the template by its final stage. RED first, both levels, plus the case that
  must stay covered.**
  Add to `tests/unit/test_base_image_preflight.py`, against `tmp_path` fixtures:
  1. `templates_missing_base_reference` reports a `Dockerfile-multi` of
     ```
     FROM ghcr.io/fasrc/a2rchi-python-base@sha256:<64 hex> AS builder
     RUN pip wheel .
     FROM docker.io/library/debian:12
     COPY --from=builder /wheels /wheels
     ```
  2. `enforce_base_images` refuses that fixture and names the template.
  3. **The over-strictness guard.** A multistage template whose final stage is `FROM builder`
     — naming the earlier a2rchi stage — is **not** reported, and `enforce_base_images` does not
     refuse it. Write this one too; a check made stricter has to be tested for becoming strict
     about the wrong thing, and copying build output back onto the a2rchi base is the ordinary
     reason to write a multistage service template.

  Run all three. The first two must fail (the early `AS builder` line satisfies today's check);
  the third may already pass — say so in the commit message either way, and keep it, because it
  is what pins the behaviour task 2.1 could regress.

  Then implement final-stage resolution in `base_image_preflight.py`:
  - Parse the template's `FROM` lines in order, capturing each stage's reference and its `AS`
    alias when present. Use a **new** pattern for this; `_FROM_BASE_RE` stays as it is, because
    `base_reference` (`:122`) shares it and must keep matching any a2rchi reference.
  - The final stage is the last `FROM`. If its reference names an earlier stage's alias, follow
    the alias back to that stage's base. Guard against a cycle with a visited set so a malformed
    template cannot hang the preflight.
  - The template is covered only when the resolved base names a member of the placeable set.
  - **Fail closed.** A reference the parser cannot resolve — `FROM ${BASE_IMAGE}`, or any form
    the pattern does not match — makes the template **reported**. The defect being fixed is a
    check that answered "covered" when it did not know.
  - Write a comment stating what the resolution does and does **not** handle: a linear chain of
    named stages, not `ARG` substitution, build args, `--platform` flags, or `COPY --from`
    provenance. State the bound you implemented rather than implying totality — implying
    totality is what let the original defect ship.

  Gate green, then commit.

## 3. Confirm nothing else regressed

- [ ] 3.1 **Sweep every caller and fixture, and re-run the reproduction.**
  `templates_missing_base_reference` is now stricter, so any fixture whose template names an
  a2rchi base outside the placeable set, or that is multistage, now reports.
  ```bash
  grep -rn "templates_missing_base_reference\|_refuse_uncoverable_templates" src/ tests/
  grep -rn "a2rchi-[a-z]*-base" tests/unit/ | grep -v "python-base\|pytorch-base"
  python -m pytest tests/unit/test_base_image_preflight.py \
    tests/unit/test_cli_create_dev_smoke.py \
    tests/unit/test_python_version_declaration.py \
    tests/unit/test_update_service_base_images.py -q
  ```
  Two things measured on `7c9915d0` that you should confirm rather than trust:
  - `a2rchi-nonesuch-base` appears at `tests/unit/test_base_image_preflight.py:844`, but only as
    a **query argument** to `base_reference`, not as a template fixture. It is not affected.
  - The `AS builder` fixtures in `tests/unit/test_update_service_base_images.py` exercise
    `scripts/dev/update_service_base_images.py`, a different module that does not call the
    coverage check. Confirm this rather than assume it.

  If a fixture does become invalid, that is a real finding about the fixture — **fix the fixture,
  do not weaken the check** — and say so in the PR body.

  `test_templates_missing_base_reference_on_real_directory_is_empty` (`:1314`) must still pass.
  If it fails, stop: a real template changed under you.

  Re-run the task 0.1 reproduction script. Both cases must now print the offending template and
  `enforce: REFUSED`. Paste that output into the PR body.

  This checkbox ends with a commit only if the sweep changed a fixture or a comment. If nothing
  needed changing, record the sweep's result in the next commit's message instead of committing
  nothing.

- [ ] 3.2 **Full gate, then the PR.**
  ```bash
  bash scripts/gate.sh
  ```
  Exit 0, no `--no-verify` anywhere. New lines in `src/cli/managers/base_image_preflight.py` are
  coverage-measured (`scripts/gate.sh` runs `--cov=src`), so confirm patch coverage clears 80% on
  a **clean** tree before believing the gate — stale `origin/dev...HEAD` line numbers scored
  against a dirty working tree give a false reading in both directions.

  Confirm the two acceptance checks that are greppable:
  ```bash
  git diff origin/dev -- src/cli/managers/base_image_preflight.py | grep '^[-+].*_FROM_BASE_RE = '
  grep -n 'a2rchi-python-base"' src/cli/managers/base_image_preflight.py
  ```
  The first must print **nothing** (`_FROM_BASE_RE` unchanged). The second must show no new
  literal duplicating `PYTHON_BASE`.

  Push with `git push -u origin fix/issue-382-placeable-base` — a branch created with
  `checkout -b ... origin/dev` tracks the trunk, and `-u` is what repoints it.

  Open the PR against `fasrc/archi:dev` with `closes #382` **in the body**, not the title — a
  closing keyword in the title does not link the issue. Record in the PR body: the placeable-set
  design decision and why `_FROM_BASE_RE` was left alone, the stated bound of the multistage
  resolution, the task 3.1 sweep result even if nothing changed, and the before/after
  reproduction output. No `Co-Authored-By` trailers. **Do not merge** — a human merges.

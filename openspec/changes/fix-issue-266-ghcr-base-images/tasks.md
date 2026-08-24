## 1. Confirm the pin

- [ ] 1.1 `model: haiku` — re-derive the current pin: find the newest commit on `origin/dev` matching the base-input pattern in `.github/workflows/publish-base-images.yml`, and confirm both `ghcr.io/fasrc/a2rchi-python-base` and `ghcr.io/fasrc/a2rchi-pytorch-base` list the resulting `dev-<sha7>` tag. Expected as of 2026-08-23: `dev-4314ac4` from `4314ac4b`. Do not trust that line — re-derive it. If the two images have no tag in common, stop and report.

## 2. Pin the templates (RED first)

- [ ] 2.1 `model: sonnet` — add the failing test to `tests/unit/test_python_version_declaration.py`: every service template whose `FROM` names an `a2rchi-*-base` image must use the `ghcr.io/fasrc/` prefix. Assert the failure message names the offending file. Watch it fail against the current `docker.io/a2rchi/` templates.
- [ ] 2.2 `model: sonnet` — add the second failing test, separately: the tag must be explicit and not `latest`, and all such references must share one tag. This is a distinct test because a prefix-only check passes a floating `ghcr.io/fasrc/...:latest`.
- [ ] 2.3 `model: haiku` — run `python scripts/dev/update_service_base_images.py --tag <pin from 1.1> --switch-source ghcr`. Confirm exactly 15 changed files, all under `src/cli/templates/dockerfiles/`, and that `Dockerfile-base`, `Dockerfile-base-gpu`, `Dockerfile-postgres`, `Dockerfile-grafana`, and both `base-*-image/Dockerfile` files are untouched. Both tests from 2.1 and 2.2 go green.
- [ ] 2.4 `model: haiku` — confirm zero `docker.io/a2rchi/` references remain under `src/cli/templates/dockerfiles/`.

## 3. Preflight: reference discovery

- [ ] 3.1 `model: sonnet` — RED: `extract_base_references()` in a new `src/cli/managers/base_image_preflight.py` returns the deduped `FROM` references for a given set of enabled services and GPU flag, reading the template files.
- [ ] 3.2 `model: opus` — RED: the `grader` case. With `grader` enabled and no GPU, the pytorch base must appear in the result. This is the test that fails any GPU-flag-based inference (design D4), so write it before the implementation and confirm it fails for the right reason.
- [ ] 3.3 `model: sonnet` — RED: a `localhost/`-prefixed reference is excluded from the returned set.
- [ ] 3.4 `model: sonnet` — GREEN: implement `extract_base_references()`.

## 4. Preflight: availability decision

- [ ] 4.1 `model: opus` — RED: `decide_availability()` as a pure function over probe results. Cover, one test each: present locally passes without a registry result; absent locally but manifest reachable passes; absent and unauthorized refuses; absent and manifest-unknown refuses; absent and unreachable refuses; no runtime passes as skipped; unrecognised probe result passes as unknown.
- [ ] 4.2 `model: opus` — GREEN: implement `decide_availability()` returning a verdict plus a cause, never raising.
- [ ] 4.3 `model: sonnet` — RED then GREEN: the Python-floor comparison runs only for locally present images. Below the floor refuses and the error names both the reported version and the `requires-python` floor; unreadable or unparseable output is an explicit unknown that passes.

## 5. Preflight: diagnostics

- [ ] 5.1 `model: opus` — RED: message composition per cause (design D3). Unauthorized names the classic-PAT + `read:packages` requirement and mentions SSO; manifest-unknown identifies a stale or deleted pin and does NOT say "log in"; unreachable names a network or registry fault. Each names the image reference.
- [ ] 5.2 `model: sonnet` — RED then GREEN: the login command in the message names `podman` under `--podman` and `docker` otherwise.
- [ ] 5.3 `model: sonnet` — GREEN: implement message composition.

## 6. Preflight: the probe seam

- [ ] 6.1 `model: sonnet` — implement the container probe behind an injected callable: image-present check, manifest-reachability check, and the version read. Every unit test in groups 3–5 injects a fake; no test shells out.
- [ ] 6.2 `model: sonnet` — map real probe exit codes and stderr onto the causes in `decide_availability()`, with unrecognised output falling through to unknown rather than to a refusal.

## 7. Wire it into `archi create`

- [ ] 7.1 `model: opus` — RED: a test proving the ordering contract — with an unobtainable base image and `--force` against an existing deployment, `remove_existing_deployment()` is never called and the deployment directory survives. This is the regression test for design D1; the issue's own stated call site fails it.
- [ ] 7.2 `model: opus` — GREEN: add the call site in `src/cli/cli_main.py` between the port check (`:262-268`) and `remove_existing_deployment` (`:278`). Keep it a thin call — all logic stays in the helper module, because lines added to `cli_main.py` that unit tests do not import fail the diff-coverage gate.
- [ ] 7.3 `model: sonnet` — RED then GREEN: `archi create --dry` on a host with no container runtime completes and prints its summary, with no preflight failure.

## 8. Documentation

- [ ] 8.1 `model: haiku` — document the one-time `docker login ghcr.io` for a clean host: classic PAT with `read:packages`, SSO authorization if enforced, and the note that fine-grained PATs cannot work. Place it with the existing deployment or install docs; do not add a credential to any config or secrets file.

## 9. Gate and review

- [ ] 9.1 `model: sonnet` — `bash scripts/gate.sh` green, patch coverage >= 80%. Check `git status` immediately after each commit: the pre-commit black writer rewrites the tree after staging.
- [ ] 9.2 `model: opus` — run `/codex:adversarial-review --wait` on the branch; verify each finding against the code, fix what holds test-first, push back with reasons on what does not. Re-run until a round returns zero findings or only nits, then file the nits as issues.
- [ ] 9.3 `model: sonnet` — open the PR against `dev` with a body per `info-pr-overview`, carrying the surviving review findings and the readiness verdict. Confirm the pr-preview smoke job is green.

## 1. Confirm the pin

- [x] 1.1 `model: haiku` — re-derive the current pin: find the newest commit on `origin/dev` matching the base-input pattern in `.github/workflows/publish-base-images.yml`, and confirm both `ghcr.io/fasrc/a2rchi-python-base` and `ghcr.io/fasrc/a2rchi-pytorch-base` list the resulting `dev-<sha7>` tag. Expected as of 2026-08-23: `dev-4314ac4` from `4314ac4b`. Do not trust that line — re-derive it. If the two images have no tag in common, stop and report.

## 2. Pin the templates (RED first)

> **Verified by dry run 2026-08-23** (rewrite applied and reverted on a clean tree): the
> command in 2.3 changes exactly 15 files, all under `src/cli/templates/dockerfiles/`, and
> leaves zero `docker.io/a2rchi` references. **Several rewritten `FROM` lines carry a trailing
> space** (`FROM ghcr.io/fasrc/a2rchi-python-base:dev-4314ac4 `), which the templates already
> had before the rewrite. Strip the line before comparing the tag — a naive
> `ref.rsplit(":", 1)[1] == tag` reads `"dev-4314ac4 "` and fails.

- [x] 2.1 `model: sonnet` — add the failing test to `tests/unit/test_python_version_declaration.py`: every service template whose `FROM` names an `a2rchi-*-base` image must use the `ghcr.io/fasrc/` prefix. Assert the failure message names the offending file. Watch it fail against the current `docker.io/a2rchi/` templates.
- [x] 2.2 `model: sonnet` — add the second failing test, separately: the tag must be explicit and not `latest`, and all such references must share one tag. This is a distinct test because a prefix-only check passes a floating `ghcr.io/fasrc/...:latest`.
- [x] 2.3 `model: haiku` — run `python scripts/dev/update_service_base_images.py --tag <pin from 1.1> --switch-source ghcr`. Confirm exactly 15 changed files, all under `src/cli/templates/dockerfiles/`, and that `Dockerfile-base`, `Dockerfile-base-gpu`, `Dockerfile-postgres`, `Dockerfile-grafana`, and both `base-*-image/Dockerfile` files are untouched. Both tests from 2.1 and 2.2 go green.
- [x] 2.4 `model: haiku` — confirm zero `docker.io/a2rchi/` references remain under `src/cli/templates/dockerfiles/`.

## 3. Preflight: which base images are required

> **Plan corrected mid-implementation.** Group 3 originally said "for each enabled service,
> read `Dockerfile-<service>`". That is wrong — the mapping is not 1:1 (`chatbot` builds
> `Dockerfile-chat`, `benchmarking` builds `Dockerfile-benchmarks`, `config-seed` builds
> `Dockerfile-chat` regardless of the chatbot). See design D4 for the two rejected ways of
> recovering the mapping and the rule that replaced it.

- [x] 3.1 `model: opus` — RED: `required_base_images(gpu_ids, grader_enabled)` returns the python base always; adds the pytorch base when `gpu_ids` is set; adds it when `grader` is enabled with no GPU; and returns python only when neither holds. Four cases, four assertions.
- [x] 3.2 `model: opus` — RED: the rule-versus-templates guard. Every template on the pytorch base must be a `-gpu` variant or `Dockerfile-grader`, and no `-gpu` template may sit on the python base. This is what stops the rule drifting away from the templates it claims to describe; verified clean against all 15 at design time.
- [x] 3.3 `model: sonnet` — RED: the returned references carry the pinned `ghcr.io/fasrc/` tag read from the templates, not a hard-coded string, so the pin and the preflight cannot disagree.
- [x] 3.4 `model: sonnet` — GREEN: implement both functions.

## 4. Preflight: availability decision

- [x] 4.1 `model: opus` — RED: `decide_availability()` as a pure function over probe results. Cover, one test each: present locally is available with no pull attempted; absent and pulled successfully is available; absent `localhost/` reference refuses without attempting a pull; absent and unauthorized refuses; absent and tag-unknown refuses; absent and registry-unreachable refuses; pull failed for lack of disk refuses with its own cause; runtime uninvokable refuses.
- [x] 4.2 `model: opus` — GREEN: implement `decide_availability()` returning a verdict plus a cause, never raising. Assert in a test that the function has no pass-with-note outcome — every result is available or refused.
- [x] 4.3 `model: opus` — RED then GREEN: the Python-floor comparison runs for EVERY reference, including one that had to be pulled. Below the floor refuses and the error names both the reported version and the `requires-python` floor. An unreadable or unparseable version ALSO refuses, with its own message — this reversed a first-draft decision to pass it with a note, so make the test assert the refusal explicitly.

## 5. Preflight: diagnostics

- [x] 5.1 `model: opus` — RED: message composition per cause (design D3). Unauthorized names the classic-PAT + `read:packages` requirement and mentions SSO; tag-unknown identifies a stale or deleted pin and does NOT say "log in"; unreachable names a network or registry fault; absent `localhost/` names the base-image build script; out-of-disk names disk exhaustion and does NOT say "log in"; unreadable version names the reference and the failed determination. Each names the image reference.
- [x] 5.2 `model: sonnet` — RED then GREEN: the login command in the message names `podman` under `--podman` and `docker` otherwise.
- [x] 5.3 `model: sonnet` — GREEN: implement message composition.

## 6. Preflight: the probe seam

- [ ] 6.1 `model: sonnet` — implement the container probe behind an injected callable: local-presence check (`image inspect`), pull, reachability (`manifest inspect`, dry mode only), and the version read. Every unit test in groups 3–5 injects a fake; no test shells out. `image inspect` and `pull` were chosen over `manifest inspect` for the real path precisely because both are uniformly supported (design D2).
- [ ] 6.2 `model: sonnet` — map real pull exit codes and stderr onto the causes in `decide_availability()`, including the out-of-disk case. An unrecognised failure maps to a refusal, not to a pass: availability has no unknown outcome by design.

## 7. Wire it into `archi create`

- [ ] 7.1 `model: opus` — RED: a test proving the ordering contract — with an unobtainable base image and `--force` against an existing deployment, `remove_existing_deployment()` is never called and the deployment directory survives. This is the regression test for design D1; the issue's own stated call site fails it.
- [ ] 7.2 `model: opus` — GREEN: add the call site in `src/cli/cli_main.py` between the port check (`:262-268`) and `remove_existing_deployment` (`:278`). Keep it a thin call — all logic stays in the helper module, because lines added to `cli_main.py` that unit tests do not import fail the diff-coverage gate.
- [ ] 7.3 `model: opus` — RED then GREEN: the dry mode refuses what the real create would refuse. `archi create --dry` pulls nothing, and refuses on a cause it can establish without pulling (unauthorized, unknown tag, absent `localhost/` base).
- [ ] 7.4 `model: opus` — RED then GREEN: a dry run DOES check the Python floor for an image already present locally. Below the floor refuses; an unreadable version refuses. This reverses a draft that disabled the version check for all dry runs, so assert the refusal — a test that only checks "dry run does not pull" passes the defect.
- [ ] 7.5 `model: opus` — RED then GREEN: the unverified marker, absent-but-reachable route. The dry run exits 0, marks that image NOT VERIFIED, and states the version cannot be read without pulling.
- [ ] 7.6 `model: opus` — RED then GREEN: the unverified marker, no-runtime route. `archi create --dry` on a host with no container runtime exits 0, and the summary marks the base images NOT VERIFIED naming the absent runtime. Assert the marker, not just the exit code — a test that checks only exit 0 passes the very defect this branch exists to fix.
- [ ] 7.7 `model: opus` — RED then GREEN: the unverified marker, unsupported-probe route. With a runtime present but the reachability probe unsupported, and a base image absent locally, the dry run exits 0 and marks the base images NOT VERIFIED naming the unsupported probe. Separate from 7.6 because the two routes reach the same state by different paths.
- [ ] 7.8 `model: sonnet` — RED then GREEN: a real create whose runtime cannot be invoked is refused before the teardown. Covers the `--podman` path, which `cli_main.py:160-170` does not check today.
- [ ] 7.9 `model: opus` — RED then GREEN: an unverified dry run must NOT print readiness language. `src/cli/utils/helpers.py:384-385` emits "Configuration and secrets are valid. Run without --dry to deploy." unconditionally today, so give the summary three mutually exclusive terminal states (ready / refused / not verified) and assert that the not-verified state prints neither that sentence nor any other deploy-now instruction. Adding a marker without removing the readiness claim leaves the contradiction intact and would pass a marker-only test.

## 8. Documentation

- [ ] 8.1 `model: haiku` — document the one-time registry login for a clean host: classic PAT with `read:packages`, SSO authorization if enforced, and the note that fine-grained PATs cannot work. Give the instruction for BOTH supported container tools — `docker login ghcr.io` and `podman login ghcr.io` — since podman does not read docker's credential store, and `--podman` is a supported deployment mode. Place it with the existing deployment or install docs; do not add a credential to any config or secrets file.

## 9. Gate and review

- [ ] 9.1 `model: sonnet` — `bash scripts/gate.sh` green, patch coverage >= 80%. Check `git status` immediately after each commit: the pre-commit black writer rewrites the tree after staging.
- [ ] 9.2 `model: opus` — run `/codex:adversarial-review --wait` on the branch; verify each finding against the code, fix what holds test-first, push back with reasons on what does not. Re-run until a round returns zero findings or only nits, then file the nits as issues.
- [ ] 9.3 `model: sonnet` — open the PR against `dev` with a body per `info-pr-overview`, carrying the surviving review findings and the readiness verdict. Confirm the pr-preview smoke job is green.

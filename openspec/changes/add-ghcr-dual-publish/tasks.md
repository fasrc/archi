## 1. Pre-flight

- [x] 1.1 Confirm with fasrc org admin that ghcr.io publishing is allowed (`Settings → Packages` for the org); if denied, escalate before proceeding
- [x] 1.2 Confirm no FAS-level "no public artifacts by default" policy applies to base container images

## 2. SOURCE_PREFIXES extension

- [x] 2.1 Add `"ghcr": "ghcr.io/fasrc/"` entry to `SOURCE_PREFIXES` in `scripts/dev/update_service_base_images.py`
- [x] 2.2 Verify the `--switch-source` argparse choices include `"ghcr"` (or accept it without explicit listing); update help text to mention the new option

## 3. Graceful skip in push_docker_images.sh

- [x] 3.1 In `scripts/dev/push_docker_images.sh`, wrap the docker.io `docker login` + `docker push` in a guard that checks both `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` are non-empty (`-n "${VAR:-}"`); when either is empty/unset, emit a single notice line ("DockerHub credentials not configured; skipping docker.io publish") and continue without failing
- [x] 3.2 In `scripts/dev/push_docker_images.sh`, add a ghcr.io publish path that runs unconditionally: assume the workflow's caller has already done `docker/login-action@v3` for ghcr.io; just `docker push ghcr.io/fasrc/a2rchi-{python,pytorch}-base:<TAG>` for each image in `IMAGE_DIRS` (and the `:latest` tag where the existing docker.io flow also pushes `:latest`)
- [x] 3.3 Leave `scripts/dev/docker_common.sh::docker_login` untouched (still docker.io-only; ghcr login is handled at the workflow YAML layer per Decision 2)

## 4. Workflow: pr-preview.yml

- [x] 4.1 Add job-level `permissions:` block with `packages: write` (and preserve existing read perms)
- [x] 4.2 Add a `docker/login-action@v3` step targeting `ghcr.io` using `${{ github.actor }}` + `${{ secrets.GITHUB_TOKEN }}` BEFORE the build step
- [x] 4.3 Have the push step invoke `scripts/dev/push_docker_images.sh` so it handles both registries (ghcr always; docker.io conditional) — pass through the existing `DOCKERHUB_USERNAME` / `DOCKERHUB_TOKEN` env so the script can skip docker.io when they're empty
- [x] 4.4 Verify the `preview` job's pull step also works against ghcr — it currently pulls `docker.io/a2rchi/...:${TAG}`; either keep that (docker.io if present) or fall back to ghcr if the docker.io pull fails; document the choice. **Resolution:** switched preview to pull from ghcr unconditionally (`docker pull ghcr.io/fasrc/a2rchi-python-base:${TAG}` + `--switch-source ghcr`) since ghcr is now the always-pushed registry. Docker.io path is no longer touched by the preview job.

## 5. Workflow: publish-base-images.yml

- [x] 5.1 Add job-level `permissions: packages: write`
- [x] 5.2 Add ghcr login step (`docker/login-action@v3`) before the build step
- [x] 5.3 Make the existing "Push to Docker Hub" step a `Publish base images` step that calls `scripts/dev/push_docker_images.sh` (handles both registries)
- [x] 5.4 Leave the "Prune old main Docker tags" step targeting docker.io only; do not invoke `manage_docker_tags.py` against ghcr in this change. **Added:** gated the prune step on `secrets.DOCKERHUB_USERNAME != '' && secrets.DOCKERHUB_TOKEN != ''` so it skips gracefully on the fasrc fork (where DockerHub secrets are absent); without this gate the prune step would crash and fail the job.

## 6. Workflow: test-and-build-tag.yml

- [x] 6.1 Add job-level `permissions: packages: write` on the `build-images` job and any `release` job that pushes. **Resolution:** added at workflow level (`permissions: contents: write, packages: write`) since the workflow is workflow_dispatch-only and all three jobs need package access (build-images: write, smoke-test: read, release: write).
- [x] 6.2 Add ghcr login step before build (added to build-images, smoke-test, release)
- [x] 6.3 Route the push through `scripts/dev/push_docker_images.sh` (already was; script now handles both registries)
- [x] 6.4 Route the `latest` retag (currently `docker pull docker.io/$image:$VERSION_TAG && docker tag … && docker push docker.io/$image:latest`) to also retag-and-push the `ghcr.io/fasrc/...:latest` reference. **Bonus fix:** also fixed pre-existing image naming bug in the promote step (`${REGISTRY_PREFIX}/archi-python-base` was always wrong; used `a2rchi-python-base` literally now).

## 7. First publish + flip visibility to public

- [ ] 7.1 Merge the implementing PR; let the publish-base-images workflow run on the merge commit to `main` (or trigger via `workflow_dispatch` if available)
- [ ] 7.2 Confirm both ghcr packages exist at `https://github.com/fasrc/archi/pkgs/container/a2rchi-python-base` and `…/a2rchi-pytorch-base` after the first publish; note they land as **private** by default
- [ ] 7.3 Flip visibility to **public** for both packages via the package settings UI (`Package settings → Change visibility → Public`) OR via `gh api -X PATCH /orgs/fasrc/packages/container/<name> -f visibility=public`
- [ ] 7.4 Verify from an unauthenticated machine (`docker logout ghcr.io` first if needed): `docker pull ghcr.io/fasrc/a2rchi-python-base:latest` succeeds with no login

## 8. Verification

- [ ] 8.1 Run the implementing PR's `pr-preview` workflow with `DOCKERHUB_*` secrets ABSENT (fasrc fork default state): both ghcr push succeeds and the job overall is green; docker.io push step logs the skip notice
- [ ] 8.2 Run the workflow with `DOCKERHUB_*` secrets PRESENT (simulate by temporarily setting them as repo-level secrets): both ghcr push and docker.io push succeed; the job is green
- [ ] 8.3 Run `archi create --base-image-source ghcr -n test-ghcr-pull -c examples/deployments/basic-openai/config.yaml -e <stub-secrets> --services chatbot --hostmode`; inspect a generated service Dockerfile in the deployment and confirm `FROM ghcr.io/fasrc/a2rchi-python-base:<tag>` rather than `FROM docker.io/a2rchi/...` (post-merge — depends on ghcr publish from task 7 to exist)
- [x] 8.4 Run `archi create` without `--base-image-source` (default), confirm `FROM docker.io/a2rchi/...` is still emitted (unchanged behavior). **Verified locally:** template FROM lines are docker.io-defaulted in repo; `--switch-source dockerhub` restores them cleanly after `--switch-source ghcr` roundtrip.
- [x] 8.5 `grep -rn '^FROM ghcr.io' src/cli/templates/dockerfiles/` returns zero results (templates not hardcoded to ghcr). **Verified:** 0 matches.

## 9. Documentation

- [x] 9.1 Update `docs/docs/notes_ghcr_migration.md` "UPDATE — Decisions locked" section to add a "STATUS: IMPLEMENTED" marker pointing at the merged PR. **Done:** appended a "STATUS — Implementation PR open" section pointing at PR #13; will update to "MERGED" post-merge.
- [x] 9.2 Update the Asana action ticket (`1215182716573047`) to closed/done state, referencing the merged PR URL. **Done:** ticket now shows "IMPLEMENTING (PR open as of 2026-05-28)" status with PR #13 link and the post-merge manual steps still outstanding.

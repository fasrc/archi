# Exploration — Move fasrc/archi base images to ghcr.io

> Status: **exploration only** — no decision yet. Captured to revisit later.
> Trigger: PR #8 and #9 land green on lint/unit-tests, but `build-base-images`
> stays red because `DOCKERHUB_USERNAME`/`DOCKERHUB_TOKEN` aren't provisioned
> on the fasrc/archi fork. ghcr.io would sidestep the secret-provisioning
> step entirely (uses the auto-injected `GITHUB_TOKEN`).

## Surface area — where `docker.io/a2rchi/...` reaches

Three tiers, only one is self-contained.

```
                ┌─────────────────────────────────────────────────────┐
                │   "docker.io/a2rchi/..." reaches in three places    │
                └─────────────────────────────────────────────────────┘
                                       │
        ┌──────────────────────────────┼──────────────────────────────┐
        ▼                              ▼                              ▼
  ┌──────────────┐            ┌──────────────────┐         ┌─────────────────────┐
  │ CI workflows │            │ 12 Dockerfiles   │         │ scripts/dev/ tools  │
  │  (3 files)   │            │ (FROM lines)     │         │ build/push/tag-mgmt │
  ├──────────────┤            ├──────────────────┤         ├─────────────────────┤
  │ pr-preview   │            │ Dockerfile-chat  │         │ build_docker_…sh    │
  │ publish-base │            │ -data-manager    │         │ push_docker_…sh     │
  │ test-and-bld │            │ -grader / -gpu   │         │ docker_common.sh    │
  │              │            │ -piazza          │         │ update_service_…py  │
  │              │            │ -mattermost      │         │ manage_docker_tags  │
  │ secrets:     │            │ -mailbox         │         │   ← DockerHub-      │
  │ DOCKERHUB_*  │            │ -redmine         │         │     specific API    │
  │              │            │ -benchmarks      │         │                     │
  │              │            │ (+ 4 *-gpu)      │         │ ALREADY has         │
  │              │            │                  │         │ SOURCE_PREFIXES =   │
  │              │            │ Templates ship   │         │  {localhost,        │
  │              │            │ via pip install  │         │   dockerhub}        │
  │              │            │ archi → end-user │         │                     │
  │              │            │ machines         │         │                     │
  └──────────────┘            └──────────────────┘         └─────────────────────┘
       fork-internal              user-facing                  half abstracted
```

Concrete file refs:

- `.github/workflows/publish-base-images.yml` (uses `secrets.DOCKERHUB_*`)
- `.github/workflows/pr-preview.yml` (build + push + pull paths, all docker.io)
- `.github/workflows/test-and-build-tag.yml` (production-release publish)
- `scripts/dev/docker_common.sh` line 50: `login docker.io --username ...`
- `scripts/dev/update_service_base_images.py` line 20: already has
  `SOURCE_PREFIXES = {"localhost": ..., "dockerhub": ...}` — extension point
  for `"ghcr"` is sitting right there.
- `scripts/dev/manage_docker_tags.py` — DockerHub-specific REST client.
  GHCR equivalent uses GitHub Packages API; different auth, different endpoints.
- `src/cli/templates/dockerfiles/Dockerfile-*` (12 files) hardcode
  `FROM docker.io/a2rchi/a2rchi-python-base:latest` (or pytorch-base).

## "Use ghcr.io" — four plausible meanings

```
┌─────────────────────────────────────────────────────────────────────┐
│  (A) CI-only swap                                                   │
│      fasrc's CI pushes to ghcr.io/fasrc, templates still pull       │
│      docker.io/a2rchi. Fixes the red push job; nobody downstream    │
│      notices. Cheapest. Also: pointless? CI builds images it then   │
│      immediately throws away.                                       │
│                                                                     │
│  (B) Full swap (fork self-sufficient)                               │
│      Templates rewritten to FROM ghcr.io/fasrc/... End users        │
│      deploying fasrc/archi pull from fasrc's registry. Clean break  │
│      from upstream's docker.io/a2rchi.                              │
│                                                                     │
│  (C) Multi-registry via existing SOURCE_PREFIXES abstraction        │
│      Add "ghcr" alongside "localhost"/"dockerhub". Operators pick   │
│      at deploy time. Templates parameterized rather than hardcoded. │
│      Principled; biggest scope.                                     │
│                                                                     │
│  (D) Dual-publish (mirror)                                          │
│      Push to both registries; default templates to one, document    │
│      the other. Most compatible, most maintenance.                  │
└─────────────────────────────────────────────────────────────────────┘
```

## Open threads — pick one to pull on later

1. **End-state for the fork.** Permanent divergence from upstream's image
   namespace, or stay close and only "own" the bits the fork's own CI
   needs? Dictates whether the target is (A) or (B).

2. **Who consumes the base images downstream?** Today `archi create` on
   a HUIT/HMS box pulls `docker.io/a2rchi/a2rchi-python-base:latest`.
   Are anyone's deployments pinned to specific tags? Changing templates
   breaks those.

3. **DockerHub-specific tooling.** `manage_docker_tags.py` prunes old
   `main-*` tags via the DockerHub REST API. For ghcr.io the analog is
   `DELETE /user/packages/container/<image>/versions/<id>`. Rewrite vs.
   keep-for-legacy is a real fork.

4. **Relationship to the failing CI.** If the *only* goal is "make
   build-base-images green on PRs without provisioning DockerHub
   secrets," (A) does it, but builds images CI throws away — feels
   wrong. (C) is principled but bigger. (D) is conservative but doubles
   publish work.

5. **Naming.** `ghcr.io/fasrc/a2rchi-python-base` (preserves history)
   vs. `ghcr.io/fasrc/archi-python-base` (matches the project name).
   Choosing now avoids a rename later.

6. **GHCR org policies.** fasrc org may need "allow GHCR publishing"
   toggled in `Settings → Packages` before this is even possible.
   Worth confirming before designing around it.

## Cheapest possible unblock (if we ever want it)

If the only objective is "make `build-base-images` green on PR #8/#9 for
now," the smallest change is option (A) limited to `pr-preview.yml`:

```yaml
permissions:
  packages: write

- uses: docker/login-action@v3
  with:
    registry: ghcr.io
    username: ${{ github.actor }}
    password: ${{ secrets.GITHUB_TOKEN }}

# replace `docker.io/a2rchi/` with `ghcr.io/fasrc/` in build/push/pull steps
```

No new repo secrets. No template changes. Doesn't touch `publish-base-images`
or `test-and-build-tag`. Lives stacked on PR #8.

But it's not obviously the right thing — the published image would be
fork-only and never consumed by templates, so it's mostly ceremony to
turn a checkmark green. Real value would be (B) or (C).

## Punted

No decision recorded. No proposal opened. Revisit when:

- The build-base-images failure blocks something we actually care about, or
- The fork formally diverges enough from upstream that it needs its own
  publishable images, or
- A user reports that pulling `docker.io/a2rchi/...` is failing for them.

---

## UPDATE — Decisions locked (2026-05-28 explore session)

Shape chosen: **path D — additive multi-registry / dual-publish**. Existing
docker.io publishing stays untouched; ghcr.io is added as an opt-in lane.

### Locked

1. **Image names on ghcr — keep `a2rchi-*` prefix.** Final paths:
   - `ghcr.io/fasrc/a2rchi-python-base`
   - `ghcr.io/fasrc/a2rchi-pytorch-base`

   Rationale: rename costs more later; preserves consistency across registries.

2. **Visibility — public.**

   Rationale: zero confidentiality benefit (base images contain only pinned
   public PyPI packages + MIT LICENSE; archi-physics's docker.io copy is
   already public; security audit confirmed no project secrets baked in).
   Cost-free (no storage/bandwidth metering on public packages). Eliminates
   `docker login` ceremony for every downstream consumer.

   Caveat: confirm there's no FAS-level "no public artifacts by default"
   policy before first push.

3. **Graceful skip — CI fails soft when DOCKERHUB_* secrets are missing.**

   CI pushes to ghcr always (uses auto-injected `GITHUB_TOKEN` with
   `packages:write`); pushes to docker.io only when both `DOCKERHUB_USERNAME`
   and `DOCKERHUB_TOKEN` are set. Missing-secret case logs a notice and
   continues; doesn't fail the job. Means fasrc PRs go green without
   provisioning DockerHub secrets, archi-physics behavior is unchanged.

### Deferred to implementation pass

- Auth refactor scope: `docker_common.sh::docker_login` parameterization vs
  sidestep via `docker/login-action@v3` for ghcr only.
- Tag retention on ghcr: write a sibling tool against GitHub Packages API,
  vs lean on per-repo retention policy, vs skip pruning entirely.
- Tag taxonomy: mirror exactly across both registries (`main-<sha>`,
  `pr-<n>`, `vX.Y.Z`, `latest`) — leaning yes.
- Service-image consumption (12 `Dockerfile-*` FROM lines): templates stay
  docker.io as default; users opt into ghcr via existing
  `archi create --base-image-source ghcr` flag (extension of
  `SOURCE_PREFIXES` in `update_service_base_images.py`); no template-level
  changes needed.

### Acceptance criteria

- New `SOURCE_PREFIXES` entry: `"ghcr": "ghcr.io/fasrc/"`
- `pr-preview.yml` + `publish-base-images.yml` + `test-and-build-tag.yml`:
  each publishes to ghcr always, to docker.io when secrets present; neither
  path fails the job if the other lacks credentials.
- ghcr packages created as **public** on first publish.
- `archi create --base-image-source ghcr` rewrites FROM lines to
  `ghcr.io/fasrc/a2rchi-{python,pytorch}-base:<tag>`.
- `archi create --base-image-source dockerhub` remains the default behavior.
- No changes to in-tree `Dockerfile-*` templates (no
  `FROM ghcr.io/...` hardcoded anywhere).
- `manage_docker_tags.py` unchanged (still docker.io-only); no equivalent
  shipped for ghcr in this change.

Tracking: Asana action ticket
[1215182716573047](https://app.asana.com/1/111770479145744/project/1207453379095415/task/1215182716573047)
(due 2026-06-05).

---

## STATUS — Implementation PR open

**PR:** [fasrc/archi#13 — feat(ci): publish base images to ghcr.io alongside docker.io](https://github.com/fasrc/archi/pull/13)
**Branch:** `feat/ghcr-dual-publish`
**OpenSpec change:** `add-ghcr-dual-publish` (local — at `openspec/changes/add-ghcr-dual-publish/`; the openspec dir is gitignored)
**Date opened:** 2026-05-28

What's done (in-PR):
- SOURCE_PREFIXES extension to add `ghcr`
- `push_docker_images.sh` rewritten for dual-publish (always-ghcr + conditional-docker.io + `--image` filter)
- All 3 CI workflows updated (pr-preview, publish-base-images, test-and-build-tag) with GHCR login + push-via-script + graceful docker.io skip
- Pre-existing image-name bug in `test-and-build-tag.yml`'s promote-to-latest fixed as a bonus

What's NOT done (post-merge manual steps):
- Confirm fasrc org permits GHCR publishing (assumed already done per ticket)
- Merge → first publish lands as private (GitHub default)
- Flip both packages to public visibility (UI or `gh api`)
- Anonymous-pull verification

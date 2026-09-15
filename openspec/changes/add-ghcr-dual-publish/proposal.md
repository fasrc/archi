## Why

The `build-base-images` CI job has failed on every PR in fasrc/archi this session (#3, #8, #9, #10, #11, #12) because `DOCKERHUB_USERNAME` / `DOCKERHUB_TOKEN` secrets aren't provisioned on the fasrc fork. Adding github.com Container Registry (ghcr.io) as a second publish target — alongside, not replacing, Docker Hub — lets fasrc CI publish via the auto-injected `GITHUB_TOKEN` with no new repo secrets to provision. Downstream consumers gain a second pull source (useful when Docker Hub rate-limits anonymous pulls). archi-physics's existing Docker Hub publishing is untouched.

## What Changes

- Add `ghcr.io/fasrc/a2rchi-{python,pytorch}-base` as a new publish target across all three CI workflows that build/push base images.
- Make Docker Hub publish steps conditional on `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` being set — missing-secret case logs a notice and continues, doesn't fail the job (graceful skip).
- Add `"ghcr": "ghcr.io/fasrc/"` to `SOURCE_PREFIXES` in `scripts/dev/update_service_base_images.py` so downstream `archi create --base-image-source ghcr` rewrites service `FROM` lines to ghcr paths.
- ghcr packages MUST be **public** on first publish (zero confidentiality value — base images contain only pinned public PyPI packages + MIT LICENSE; this matches archi-physics's existing public Docker Hub setup).
- **Not** changing: in-tree Dockerfile templates (the 12 `Dockerfile-*` `FROM` lines stay defaulted to `docker.io/a2rchi/...`); `manage_docker_tags.py` (still Docker Hub-only); image names (stay `a2rchi-*` for cross-registry consistency).

## Capabilities

### New Capabilities
- `registry-publishing`: how the project publishes base images to one or more container registries, including auth, conditional publishing, and the registry-source abstraction consumed by `archi create`.

### Modified Capabilities
- (none — `registry-publishing` is a new capability; no existing spec covers this surface today)

## Impact

- **Workflows:** `.github/workflows/pr-preview.yml`, `.github/workflows/publish-base-images.yml`, `.github/workflows/test-and-build-tag.yml`
- **Build helpers:** `scripts/dev/push_docker_images.sh` (graceful skip logic), `scripts/dev/docker_common.sh` (ghcr login path), `scripts/dev/update_service_base_images.py` (SOURCE_PREFIXES extension)
- **Permissions:** workflows that push to ghcr need job-level `permissions: packages: write`
- **First-time setup (one-off, outside this change):** fasrc org admin confirms ghcr.io publishing is allowed in `Settings → Packages`; first publish lands as private and gets flipped to public via the package's GitHub settings page (or scripted with `gh api`)
- **Unchanged:** archi-physics CI behavior (still pushes to docker.io with its existing secrets); current `archi create` default base-image source (still `dockerhub`); 12 in-tree `Dockerfile-*` `FROM` lines; `manage_docker_tags.py`

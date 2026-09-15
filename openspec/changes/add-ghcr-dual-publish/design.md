## Context

The base images for archi (`a2rchi-python-base`, `a2rchi-pytorch-base`) are built and published to Docker Hub at `docker.io/a2rchi/...` by three CI workflows in `archi-physics/archi` (the upstream). The fork `fasrc/archi` inherits those workflows but lacks the `DOCKERHUB_USERNAME` / `DOCKERHUB_TOKEN` secrets — every PR on the fork goes red on the `build-base-images` job for that reason alone.

The repo already has a half-built abstraction for switching registry sources at `archi create` time: `scripts/dev/update_service_base_images.py` carries

```python
SOURCE_PREFIXES = {
    "localhost":  "localhost/a2rchi/",
    "dockerhub":  "docker.io/a2rchi/",
}
```

This extends it. Three workflows publish base images: `.github/workflows/pr-preview.yml`, `.github/workflows/publish-base-images.yml`, `.github/workflows/test-and-build-tag.yml`. Auth for ghcr.io in CI uses the auto-injected `GITHUB_TOKEN` (no new repo secret to provision) given a job-level `permissions: packages: write` block.

A prior security audit (this session) verified that the base images carry zero project secrets: the build context is exactly `{Dockerfile, LICENSE, requirements.txt}` per image; Dockerfiles `COPY` only those two files; no `ARG`, `ENV`, or build secrets are declared. Publishing publicly to ghcr is therefore information-equivalent to the existing public docker.io publish — no new exposure.

Stakeholders: fasrc/archi contributors (gain a green CI without provisioning Docker Hub secrets), archi-physics/archi maintainers (their behavior is unchanged), end-user operators running `archi create` (gain a second pull source).

## Goals / Non-Goals

**Goals:**
- Make `build-base-images` green on fasrc PRs without provisioning new repo secrets.
- Give end users a second pull source via the existing `--base-image-source` flag.
- Keep archi-physics's Docker Hub publishing behavior identical.
- Use the existing `SOURCE_PREFIXES` abstraction rather than introducing a parallel one.

**Non-Goals:**
- Removing or deprecating docker.io publishing. Docker Hub remains the default for both publish and pull.
- Rewriting in-tree service `Dockerfile-*` templates to reference ghcr.io. The 12 templates stay defaulted to docker.io; users opt in via `--base-image-source ghcr`.
- Building a tag-retention/pruning tool for ghcr. `manage_docker_tags.py` stays Docker Hub-specific. Retention on ghcr relies on GitHub's per-repo package retention policy (or accumulates harmlessly).
- Migrating service images (Dockerfile-chat, -data-manager, etc.) to ghcr. Only base images are in scope.
- Making ghcr packages private. They are public from first publish — see Decision 3 below.

## Decisions

### Decision 1: Additive registry list, not registry abstraction
Each workflow publishes to ghcr **always** (using `GITHUB_TOKEN`) and to docker.io **only when both DOCKERHUB_* secrets are set**. The two publishes are parallel steps, each independently fallible.

**Why:** the simplest path that meets the Goals. A "registry abstraction" (one loop over a configurable list) would be cleaner code but introduces a new concept that the existing scripts don't need today.

**Alternative considered:** a single `publish-to-registries.sh` script that takes a `--registry` arg and iterates over a configurable list. Rejected — too much abstraction for two targets; obscures the conditional-skip logic.

### Decision 2: Sidestep `docker_common.sh::docker_login` for ghcr, use `docker/login-action@v3` directly in workflow YAML
The existing helper `scripts/dev/docker_common.sh::docker_login` is hardcoded to `docker login docker.io`. Rather than refactor it to take a `--registry` argument (a wider blast radius), the new ghcr login uses the official `docker/login-action@v3` directly in each workflow file. The existing `docker_common.sh::docker_login` is left untouched and continues to handle the docker.io path (now wrapped in a conditional).

**Why:** smaller blast radius. The script keeps doing one thing well; the workflow YAML is the right place to compose two registry logins.

**Alternative considered:** parameterize `docker_common.sh::docker_login` to take a registry argument. Rejected — would force every caller (workflows, local dev) to update, and there's no other caller that needs ghcr today.

### Decision 3: Public visibility on first publish
ghcr packages must be **public**.

**Why:** zero confidentiality benefit (security audit confirmed no project secrets in base images; archi-physics already publishes identical content publicly to docker.io); eliminates `docker login ghcr.io` ceremony for every downstream consumer; no storage/bandwidth metering on public packages; matches archi-physics's existing posture.

**Alternative considered:** private with per-package access grants to `fasrc/*` repos and named collaborators. Rejected — pure ceremony for these specific images; the only people inconvenienced would be downstream consumers running `archi create --base-image-source ghcr` on their own boxes, who'd have to provision a PAT for no protection benefit.

**Caveat:** if FAS has a "no public artifacts by default" policy this should be confirmed with whoever owns it before first push.

**Mechanics:** the first publish lands the package as **private** by default (GitHub policy). A one-off post-publish step flips visibility — either through the package's GitHub UI page (`https://github.com/orgs/fasrc/packages/container/<name>/settings`) or via `gh api -X PATCH /orgs/fasrc/packages/container/<name> -f visibility=public`. This is a one-time per-package action, not a recurring workflow step.

### Decision 4: Graceful skip via env-presence check, not workflow-level `if:` gates
The conditional docker.io publish lives **inside** `scripts/dev/push_docker_images.sh` as a credentials-presence check, not as a workflow-level `if: ${{ secrets.DOCKERHUB_TOKEN != '' }}` gate.

**Why:** keeps the workflow YAML simple (single step that calls the script) and keeps the logic testable in one place (`scripts/dev/push_docker_images.sh` can be invoked locally with various env states to verify skip behavior). Centralizes the message "DockerHub credentials not configured; skipping" in one location.

**Alternative considered:** workflow-level `if:` gates. Rejected — duplicates the conditional across 3 workflow files, and GitHub Actions secret-empty checks have subtle gotchas (the secret context returns empty string in forks for non-existent secrets, which is fine, but reading it triggers the "secret accessed" telemetry even for the empty case).

### Decision 5: Use `archi delete --name <n>` semantics, not introduce a new teardown
Out of scope for this change but worth recording: a related task (selective-service-deploy 4.2) discovered that `archi remove` doesn't exist — `archi delete --name <n>` is the correct command. No action here, just don't reintroduce the confusion.

## Risks / Trade-offs

- **Risk: First ghcr publish lands as private and silently fails downstream pulls until visibility is flipped.**  
  → Mitigation: tasks.md includes an explicit post-publish step to flip visibility to public for both packages, with verification (`docker pull` from anonymous machine). Document this as a one-off, not a recurring step.

- **Risk: `push_docker_images.sh` env-presence check is too lenient — accepts whitespace as "set."**  
  → Mitigation: bash test uses `-n "${DOCKERHUB_USERNAME:-}"` and `-n "${DOCKERHUB_TOKEN:-}"` (rejects unset and empty); explicitly skip if either contains only whitespace.

- **Risk: ghcr push fails due to org-level package policy on fasrc.**  
  → Mitigation: tasks.md includes a pre-flight check (org admin confirms ghcr publishing is allowed in `Settings → Packages` before the first workflow run); if denied, the change can't proceed and we fall back to the original "DockerHub secrets must be provisioned" path.

- **Risk: ghcr storage/bandwidth bills if we accidentally make packages private later.**  
  → Mitigation: scenarios in spec.md assert public visibility; archive step manually verifies; package settings show visibility prominently.

- **Trade-off: docker.io and ghcr go out of sync on tag retention.**  
  → Accepted: `manage_docker_tags.py` continues to prune Docker Hub tags; ghcr accumulates untagged versions until either GitHub's retention policy reaps them or we write a sibling pruner. Acceptable for the foreseeable future given the low publish frequency.

## Migration Plan

1. **Pre-flight (manual, one-off, before merging this change):** confirm fasrc org permits ghcr publishing in `Settings → Packages`. If yes, proceed; if no, escalate.
2. **Land the change:** ship the workflow + script edits as a single PR against fasrc/archi `dev`. CI for this PR will be the first to exercise the new ghcr push.
3. **First successful publish lands packages as private** (GitHub default).
4. **Flip visibility to public** (one-off, per package, via UI or `gh api`).
5. **Verify** `docker pull ghcr.io/fasrc/a2rchi-python-base:latest` works from an unauthenticated machine.
6. **Rollback path (if needed):** revert the PR. archi-physics behavior is unchanged throughout; rolling back fasrc loses only the ghcr publish and restores the prior "CI red on missing DockerHub secrets" state.

## Open Questions

- (Resolved during exploration) **Image name on ghcr** — keep `a2rchi-*` prefix.
- (Resolved during exploration) **Visibility** — public.
- (Resolved during exploration) **Push-side semantics** — ghcr always, docker.io conditional.
- (Deferred) **Auth refactor scope of `docker_common.sh`.** This change uses `docker/login-action@v3` for ghcr only; `docker_common.sh::docker_login` stays docker.io-only. If a future need arises to log into ghcr from a local script, revisit then.
- (Deferred) **ghcr tag retention/pruning.** Live with accumulation for now; revisit if untagged versions get unwieldy.
- (Deferred) **Tag taxonomy mirroring.** Both registries get the same tags (`main-<sha>`, `pr-<N>`, `vX.Y.Z`, `latest`) in this change. If ghcr-specific tags ever become useful (e.g. `nightly`), revisit.

## Context

`archi create` builds service images through `docker compose up --build`
(`src/cli/managers/deployment_manager.py:51`). The host container daemon resolves each
template's `FROM` line, so archi cannot hand it a token or a credential — the daemon reads
`~/.docker/config.json` and nothing else.

The 15 service templates currently name `docker.io/a2rchi/a2rchi-{python,pytorch}-base:latest`.
That repository is upstream-owned and floating, and the image it currently serves has Python
3.10.20 against a declared floor of `>=3.11` (`pyproject.toml:5`). The fork publishes its own
equivalents to ghcr via `.github/workflows/publish-base-images.yml`, tagged `<branch>-<sha7>`.

Measured on 2026-08-23: both `ghcr.io/fasrc/a2rchi-python-base` and
`ghcr.io/fasrc/a2rchi-pytorch-base` have `internal` visibility, are owned by `fasrc`, and are
linked to `fasrc/archi`. `internal` already grants pull to every `fasrc` org member, so no
package permission needs to change. An anonymous pull returns HTTP 401; a classic PAT with
`read:packages` returns HTTP 200 for both. Fine-grained PATs carry no Packages permission at
all and can never work (`fasrc/archi#322`).

The binding local constraint is the existing `cli-create-preflight` contract: no destructive
step may precede a step that can refuse the deployment. `remove_existing_deployment` sits at
`src/cli/cli_main.py:278`, immediately after the pure port checks at `:262-268`.

## Goals / Non-Goals

**Goals:**
- Service images build on a clean host, from base images this fork controls at a pinned tag.
- A host that cannot obtain a base image is told so by name, before any destructive action,
  with the exact remedy.
- The pin cannot silently regress to an upstream or floating tag.

**Non-Goals:**
- Changing ghcr package visibility. The packages stay `internal`.
- Storing, reading, or writing a registry credential inside archi, or running
  `docker login` on the operator's behalf, or modifying `~/.docker/config.json`.
- Making `archi create` build the base image itself.
- Automating future pin bumps when a later base change publishes a new `dev-<sha7>`.

## Decisions

### D1 — The preflight runs above the teardown, not above compose

`fasrc/archi#266` says to call the preflight "before `DeploymentManager.start_deployment`"
(`cli_main.py:320`). That location is **after** `remove_existing_deployment` (`:278`) and
would regress the `cli-create-preflight` requirement "No destructive step precedes a step
that can refuse the deployment". A preflight is by definition a step that can refuse.

The call site is therefore between the port check (`:268`) and the teardown (`:278`), inside
the block the existing code comments describe as "everything above this line can still refuse
the deployment". Rejected: the issue's stated location, because a `--force` create that was
always going to fail would first destroy a working deployment.

### D2 — Availability is decided local-first, and a reachable-but-unpulled image passes

The check is, per distinct base reference, in order:

1. The image is present locally (`image inspect`) → **pass**, with no network access. A host
   that already pulled or built the base needs no login, and the preflight must never demand
   one from it.
2. Otherwise its manifest is reachable with the host's existing credentials
   (`manifest inspect`) → **pass**. Compose will pull it.
3. Otherwise → **refuse**, classified by cause (D3).

Rejected: probing the registry first. It would put a network round trip and a possible
false refusal in front of hosts that are already fully provisioned, including every
air-gapped or offline rebuild.

### D3 — The diagnostic is classified by cause, because the remedies differ

| Cause | What the operator must do |
|---|---|
| Authentication refused (401/403/denied) | `docker login ghcr.io` with a **classic** PAT carrying `read:packages`; authorize it for SSO if enforced |
| Manifest unknown / tag absent | The pin is stale or the tag was deleted — re-run `scripts/dev/update_service_base_images.py` |
| Registry unreachable | Network or registry outage; nothing to fix in archi |

Collapsing these into one message sends operators to `docker login` for a stale pin, which
cannot work. The authentication case additionally names the classic-PAT requirement,
because the natural first attempt — a fine-grained PAT — fails with an
indistinguishable "denied" and no hint as to why.

### D4 — Base references are parsed from the template `FROM` lines, never inferred

`Dockerfile-grader` is a non-GPU service that nonetheless builds on the **pytorch** base.
Any rule of the form "GPU implies pytorch, otherwise python" is therefore wrong, and would
check an image the deployment does not use while skipping one it does.

All 15 `FROM` lines are literal — no Jinja — so they are parseable directly from the
templates. This matters because rendering (`prepare_deployment_files`, `:310`) happens after
the teardown; a preflight that needed rendered output could not satisfy D1.

Template selection mirrors the compose template: for each enabled service, `Dockerfile-<service>`
plus the `-gpu` suffix when `gpu_ids` is set. References with a `localhost/` prefix are
skipped — a locally built base is the "present locally" case by construction.

### D5 — The Python floor is checked only when the image is already local

Reading an image's Python version means running it (`run --rm --entrypoint python <ref> -V`),
which requires the image on the host. Forcing a pull to satisfy a preflight would make the
preflight more expensive than the build step it guards.

So the version comparison runs only in D2 case 1, where the image is already present and the
check is nearly free. In case 2 the reachability result stands alone. An unreadable or
unparseable version is an explicit `UNKNOWN` outcome that passes with a logged note — never
a crash, and never a refusal, because failing a deployment over a probe that did not work is
worse than the mismatch it was looking for.

### D6 — The container tool follows the deployment's own choice

The probe and the remedy text use `podman` when `--podman` is in effect and `docker`
otherwise, matching `DeploymentManager.compose_tool`. Printing a `docker login` instruction
to a podman operator is a wrong instruction.

### D7 — No runtime, no preflight

When no container runtime is available the preflight is skipped with a logged note rather
than failing. `--dry` deliberately requires no runtime (`cli_main.py:155-160`), and a dry run
must not start requiring one. Where a runtime does exist, the preflight runs even under
`--dry`, since it is read-only and improves the dry run's fidelity for free.

### D8 — Pure logic in a helper module, thin call site

All decision logic lives in `src/cli/managers/base_image_preflight.py`, with the container
calls behind an injected seam. `cli_main.py` gets a call site only. This follows the same
rule that governs `app.py`: lines added to an entry-point file that unit tests do not import
fail the diff-coverage gate.

## Risks / Trade-offs

- **The pin goes stale.** A later base-input commit publishes a new `dev-<sha7>` and the
  templates keep pointing at `dev-4314ac4`. → Accepted and out of scope per the issue. The
  build stays correct because the pinned image still exists; only "newest" is lost. D3's
  manifest-unknown branch gives an actionable error if the tag is ever deleted.
- **The preflight adds a network round trip** for hosts without the image locally. → Bounded
  to one manifest request per distinct base reference, at most two, and only for references
  not already present locally.
- **A false pass is possible**: the manifest is reachable but the pull later fails (rate
  limit, disk, a race with tag deletion). → Accepted. The preflight is defense in depth; it
  narrows the common failure, and compose still reports the rest.
- **`manifest inspect` is not universally supported** across daemon versions and podman. →
  An unsupported or unrecognised probe result is `UNKNOWN` and passes with a note (D5's rule
  applied to reachability). The preflight must never block a deployment that would otherwise
  have worked.
- **Operators must log in once**, which the old acceptance criterion said they would not have
  to. → Unavoidable against `internal` packages, and recorded on the issue. The alternative
  the operator rejected was making the packages public.

## Migration Plan

The template change takes effect for any deployment created after the merge; existing
running deployments are untouched until their next `archi create`. Rollback is the inverse
rewrite: `update_service_base_images.py --switch-source dockerhub --tag latest`.

Operators on a clean host need one `docker login ghcr.io` with a classic PAT carrying
`read:packages` before their first `archi create`. Hosts that already hold a locally built
base image are unaffected — D2 case 1 passes them without a registry call.

## Open Questions

None. The visibility question was settled by the operator on 2026-08-23 (packages stay
`internal`; the access model is fixed instead), and the credential class is settled by
GitHub's own constraint that only classic PATs carry Packages scopes.

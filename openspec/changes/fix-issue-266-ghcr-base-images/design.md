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

### D2 — Availability is decided by materializing the image, not by probing a manifest

Per distinct base reference, in order:

1. Present locally (`image inspect`) → **available**, no network access.
2. Otherwise, if the reference carries a `localhost/` prefix → **refuse**. A `localhost/`
   reference is what `scripts/dev/build_docker_images.sh:109` tags a locally built base as.
   It is a registry-style reference, not evidence of presence: a fresh or pruned daemon
   resolves it to nothing, and there is no registry to pull it from. The remedy is to build
   the base image, so the message names the build script.
3. Otherwise → **pull it**. Success makes it available *and local*. Failure refuses,
   classified by cause (D3).

Rejected: checking manifest reachability and letting compose do the pull. Reachability is
not compatibility — it proves a tag resolves, not that the image behind it satisfies the
Python floor — so a clean host would pass the preflight, lose its existing deployment to the
`--force` teardown, and only then meet the interpreter mismatch this whole change exists to
prevent. Pulling is not extra work: compose pulls the same image moments later. The only
thing that changes is that it now happens while refusing is still free.

Pulling also removes a portability problem. `manifest inspect` is not uniformly supported
across daemon versions and podman, whereas `image inspect` and `pull` are. The design no
longer needs an "unsupported probe" outcome for availability at all.

Rejected: pinning by digest instead. A digest proves identity, not compatibility, so it does
not close this hole; it would also mean changing `update_service_base_images.py` and the
release rewrite at `test-and-build-tag.yml:154`, both of which are tag-based, and the issue
puts pin mechanics out of scope. Worth revisiting separately.

### D3 — Pull failures are classified by cause, because the remedies differ

| Cause | What the operator must do |
|---|---|
| Authentication refused (401/403/denied) | Log in with a **classic** PAT carrying `read:packages`; authorize it for SSO if enforced |
| Manifest or tag unknown | The pin is stale or the tag was deleted — re-run `scripts/dev/update_service_base_images.py` |
| Registry unreachable | Network or registry outage; nothing to fix in archi |
| Out of disk | Free space and retry; the message names the reference whose pull ran out |
| `localhost/` base absent | Build the base image with `scripts/dev/build_docker_images.sh` |

Collapsing these sends operators to `docker login` for a stale pin, which cannot work. The
authentication case additionally names the classic-PAT requirement, because the natural first
attempt — a fine-grained PAT — fails with an indistinguishable "denied" and no hint as to why.

### D4 — Base references are parsed from the template `FROM` lines, never inferred

`Dockerfile-grader` is a non-GPU service that nonetheless builds on the **pytorch** base.
Any rule of the form "GPU implies pytorch, otherwise python" is therefore wrong, and would
check an image the deployment does not use while skipping one it does.

All 15 `FROM` lines are literal — no Jinja — so they are parseable directly from the
templates. This matters because rendering (`prepare_deployment_files`, `:310`) happens after
the teardown; a preflight that needed rendered output could not satisfy D1.

Template selection mirrors the compose template: for each enabled service, `Dockerfile-<service>`
plus the `-gpu` suffix when `gpu_ids` is set. No reference is exempted from the checked set —
see D2 step 2 for why `localhost/` in particular is not a safe exemption.

### D5 — The Python floor is checked for every base image, and an unreadable version refuses

D2 guarantees that any reference reaching this point is present on the host, so the version
comparison (`run --rm --entrypoint python <ref> -V`) applies uniformly. There is no
local-only carve-out, and therefore no clean-host blind spot: the case the issue was filed
about is exactly the case D2 step 3 materializes.

A version that cannot be read or parsed **refuses the deployment**, with its own diagnostic
naming the reference. An earlier draft passed this case with a logged note, which was wrong:
it converted an unknown compatibility result into permission to tear down a working
deployment, in the exact safety property this change exists to provide. There is also no
false-refusal cost to speak of — the probe runs a container, and `docker build` runs
containers too, so a host that cannot run the probe cannot complete the build either.

The preflight now has no pass-with-note branch at all. Every outcome is available or refused.

### D6 — The container tool follows the deployment's own choice

The probe and the remedy text use `podman` when `--podman` is in effect and `docker`
otherwise, matching `DeploymentManager.compose_tool`. Printing a `docker login` instruction
to a podman operator is a wrong instruction.

### D7 — `--dry` runs a non-mutating preflight; a real create requires a runtime

An earlier draft skipped the preflight entirely under `--dry`. That broke the pattern the
existing `cli-create-preflight` spec sets — dry runs mirror the refusals a real run would
make on the same inputs — and left dry runs silent about the very failure modes this change
introduces. An operator with no registry login would get a clean dry-run summary and a
refusal on the real create.

So `--dry` runs the preflight in a **non-mutating mode**: parse the references, check local
presence, and for an absent reference check registry reachability and authorization
*without* pulling. A deterministic failure it can establish this way — unauthorized, unknown
tag, an absent `localhost/` base — refuses the dry run, matching what the real create would
do. The pull itself never happens under `--dry`, so no host state changes and no
multi-gigabyte download occurs.

Reachability in this mode uses `manifest inspect`, whose support varies across daemons. Here
that is acceptable where it was not for the real path: an unsupported probe under `--dry`
produces a note rather than a refusal, because a dry run destroys nothing and an advisory
gap costs the operator nothing.

`--dry` still requires no container runtime. `cli_main.py:155-160` makes that an explicit
decision, and this change does not overturn it: with no runtime, the dry preflight is skipped
with a note. On a **real** create the opposite rule holds — an uninvokable runtime refuses,
because compose needs the same runtime minutes later, so standing down would only move the
failure past the teardown. `cli_main.py:160-170` already enforces this for docker; it does
not check podman when `--podman` is given, and the preflight closes that gap because it needs
the runtime itself.

### D8 — Pure logic in a helper module, thin call site

All decision logic lives in `src/cli/managers/base_image_preflight.py`, with the container
calls behind an injected seam. `cli_main.py` gets a call site only. This follows the same
rule that governs `app.py`: lines added to an entry-point file that unit tests do not import
fail the diff-coverage gate.

## Risks / Trade-offs

- **The pin goes stale.** A later base-input commit publishes a new `dev-<sha7>` and the
  templates keep pointing at `dev-4314ac4`. → Accepted and out of scope per the issue. The
  build stays correct because the pinned image still exists; only "newest" is lost. D3's
  unknown-tag branch gives an actionable error if the tag is ever deleted.
- **The preflight pulls before the teardown**, so a `--force` create on a clean host waits
  for a multi-gigabyte pytorch base before anything is destroyed. → Accepted, and the reason
  for the design. Total time is unchanged, because compose would pull the same image moments
  later; only the ordering moves, and it moves so that a failure is still free.
- **Does pulling first make disk exhaustion more likely?** Reviewed and rejected as a
  sequencing concern, on the evidence: `remove_existing_deployment` calls
  `delete_deployment(remove_images=False, remove_volumes=False)`
  (`src/cli/utils/helpers.py:343-346`), so the teardown reclaims the deployment directory
  only — no image layers and no volumes. There is no multi-gigabyte reclaim for the pull to
  have benefited from, so ordering does not change how much space is available. A pull that
  runs out of space is still classified as its own cause with its own remedy (D3), because
  the generic failure text would send the operator to `docker login` for a full disk.
- **A false pass is still possible**: the image pulls and satisfies the floor, but the build
  fails later for an unrelated reason (disk, network, a dependency). → Accepted. The
  preflight is defense in depth against one known failure class, not an oracle for the build.
- **The version probe can malfunction** on an image that is present. → It refuses (D5). The
  cost is a possible false refusal on a host whose runtime cannot start a container, which is
  a host that could not have completed the build either.
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

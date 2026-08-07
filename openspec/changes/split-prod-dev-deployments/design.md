## Context

`holygpu7c0717.rc.fas.harvard.edu` (DNS alias `archi.rc.fas.harvard.edu`) currently hosts one archi deployment:

```
~/.archi/archi-archi-openai-compat/
  containers: chatbot/data-manager/postgres/grafana-archi-openai-compat
  network_mode: host on every service
  ports: chatbot 7861, data-manager 7889, grafana 3000
  bind mount on chatbot: /home/a2rchi/archi-openai-compat/src → /root/archi/src
  vLLM client: base_url http://localhost:8000/v1 (host-local vLLM)
```

The chatbot container is built from `chatbot-archi-openai-compat:2000` but its application source is shadowed at runtime by a bind mount of the host checkout. That checkout has been on whatever feature branch we are working on at the time. So today, users are served whatever branch is currently checked out at `~/archi-openai-compat`.

Other named deploys already coexist on this host (`archi-ragas-test`, `archi-main-gpu-agent`), proving the archi CLI's per-deploy isolation works — separate container names, volumes, and ports keyed off `config.yaml :: name`. The CLI itself exposes a `--dev` flag (`cli/cli_main.py:57`) that toggles the source bind mount on or off, and the existing `dev-mode-mounts` spec already governs what it does.

The constraint set for this change:
1. Production cannot be impacted by in-flight feature work.
2. Dev must follow `dev`-branch HEAD with a fast iteration loop.
3. Only the vLLM server may be shared between the two.
4. Staff need to reach dev from outside the host.

## Goals / Non-Goals

**Goals:**
- Two coexisting deployments — `archi-openai-compat` (prod) and `archi-openai-compat-dev` (dev) — with disjoint container sets, postgres, vectorstores, grafana, host ports, and source trees.
- Prod runs *without* `--dev`. Its chatbot image has code baked in at build time. Editing the host filesystem cannot affect what users see.
- Dev runs *with* `--dev` from a second checkout at `/home/a2rchi/archi-openai-compat-dev` pinned to the `dev` branch. Edit → restart chatbot → see change.
- Both deployments use the same vLLM endpoint (`http://localhost:8000/v1`).
- Staff reach dev via a documented external port on `archi.rc.fas.harvard.edu`.

**Non-Goals:**
- No automation around "follow dev HEAD." Updating dev is `git pull && archi deploy --dev` run by a human.
- No service-image publishing to GHCR. Prod's chatbot image is built locally during the prod redeploy; CI-driven service images are a follow-up.
- No GPU isolation. Both deploys share the same vLLM and therefore the same GPU(s). Resource contention is acknowledged but not addressed here.
- No subdomain or reverse-proxy work. Dev gets a port, not a pretty URL.
- No archi CLI changes. Everything in this change is operator-side configuration and runbook.
- No per-PR ephemeral preview deploys. That is a separate, larger change.

## Decisions

### Decision 1 — Prod stabilization is a redeploy without `--dev`, not just a git tag

Tagging the prod checkout was considered first because it is the smallest mechanical change. It was rejected: as long as `--dev` is on, the chatbot reads files from the host on every Python import. Any `git switch` (intentional or accidental) in `~/archi-openai-compat` propagates to users. A tag pin would discipline the operator but would not enforce the isolation.

Running prod without `--dev` removes the bind mount entirely. The chatbot image is built once with `COPY src/ ...` in its Dockerfile, and the container can no longer see the host filesystem for its application code. The host checkout becomes irrelevant to what users see; only a new `archi deploy` (rebuilding the image) can change prod behavior.

Trade-off: the iteration loop for a prod hotfix gets longer. Today the operator edits a file and restarts a container; after this change, the operator edits a file in the dev tree, tests on dev, picks a SHA, checks it out in the prod tree, and runs `archi deploy` to rebuild and replace the prod image. This is the explicit goal — prod changes go through a release flow, not an ssh session.

### Decision 2 — Deploy name `archi-openai-compat-dev` (suffix, not separate top-level)

Naming the second deploy with a `-dev` suffix on the existing name (rather than something unrelated like `archi-staging`) keeps the connection to its production sibling obvious in `docker ps`, in volume listings, and in container logs. The archi CLI cascades `config.yaml :: name` into container names (`chatbot-<name>`), image names (`chatbot-<name>:<tag>`), and volume names. The suffix means every artifact for the dev deploy is alphabetically adjacent to its prod equivalent.

Alternatives considered:
- `archi-dev` — shorter, but loses the connection to the prod deploy name. Confusing in a future where there are multiple product deployments.
- `archi-staging` — implies a release-candidate environment that mirrors prod data. We are not building that here.

### Decision 3 — Port allocation: +30 offset

Existing port use:
```
prod chatbot      7861
prod data-mgr     7889
prod grafana      3000
ragas-test chat   7881  (already uses +20)
```

Dev gets:
```
dev chatbot       7891
dev data-mgr      7919
dev grafana       3030
```

The +30 offset keeps a contiguous, predictable block per deploy and leaves room for the +20 slot to remain ragas-test's. A future fourth deploy could take +40, etc.

Alternative considered: `+100` (7961 / 7989 / 3100). Rejected as less compact; the ports are scattered enough already.

### Decision 4 — Second working tree, not a git worktree

Two ways to keep prod and dev on different branches without conflicting checkouts:
- a second `git clone` to `/home/a2rchi/archi-openai-compat-dev`
- `git worktree add ../archi-openai-compat-dev dev`

The worktree saves a `.git/` directory and makes branch coordination tighter. We are choosing the second clone because:
1. The prod tree is going to sit on a tag indefinitely. The dev tree is going to track `dev`. Linking them via worktree saves disk but offers no day-to-day benefit when neither tree is moving relative to the other.
2. A worktree shares submodule state and hooks. If we accidentally add a hook in one tree, the other inherits it.
3. The disk cost of a second clone is trivial on `/scratch`.
4. Two clones makes it impossible to accidentally check out the dev branch in the prod tree — there is no shared HEAD pointer.

### Decision 5 — Dev data starts empty

The clarification was "everything separate." That has two flavors: empty, or seeded from a prod snapshot. We are going with empty for the initial cutover. Reasoning:
- Snapshotting prod postgres + vectorstore at deploy time is a one-shot operation that becomes stale immediately. The first time dev needs realistic data, the operator can dump prod and load it into dev as a one-time refresh.
- An empty dev DB makes the cutover much simpler — no copy step, no schema-version coordination.
- Most dev work tests code paths that do not require populated retrieval data.

Trade-off: feature work that depends on realistic embeddings (RAG quality, retrieval relevance) will need an explicit "seed dev from prod" step. That step is not part of this change.

### Decision 6 — Secrets: shared values, separate paths

Dev needs OpenAI/Anthropic/HF API keys. The simplest options are:
- copy the secret files into `~/.archi/archi-archi-openai-compat-dev/secrets/`
- symlink them from prod's secrets directory

We will copy. Symlinking creates a hidden coupling — rotating a prod secret silently rotates dev. Copying makes it explicit that "dev got the same key value at deploy time" and lets the two diverge later (e.g., separate billing tags).

The postgres password is the one secret that **must** differ between prod and dev. Defensive: if a config file is wrong and the dev chatbot tries to connect to prod's postgres, the wrong password causes a connection failure instead of a successful write to the wrong database.

### Decision 7 — Staff access via external port on the existing hostname

Three access patterns were on the table:
- different port on the existing hostname (`archi.rc.fas.harvard.edu:7891`)
- subdomain (`dev-archi.rc.fas.harvard.edu`)
- reverse-proxy subpath (`archi.rc.fas.harvard.edu/dev/`)

The subdomain requires DNS work outside this server. The subpath requires reconfiguring whatever currently proxies port 7861, which we have not investigated and which is not in scope. The port-only solution requires exactly one new iptables rule and zero coordination outside this host. It is ugly but unblocking, and a nicer URL can be a follow-up.

### Decision 8 — Update loop documented, not automated

Cron / webhook / GitHub Action triggers were all rejected for this change. The dev deploy has one operator (the user) updating it on demand. A cron job would create surprise mid-debugging pulls. A webhook adds new infra. A `workflow_dispatch` button would be useful eventually but is out of scope.

The runbook entry:
```
cd ~/archi-openai-compat-dev
git pull
archi deploy --dev       # rebuilds chatbot image if Dockerfile/requirements changed,
                         # otherwise just restarts containers
```

## Risks / Trade-offs

- **[Risk]** Operator edits files in `~/archi-openai-compat` (the prod tree) out of habit and is confused when nothing happens, then runs `git switch` and breaks future prod redeploys. **Mitigation:** put a `.git/info/exclude` or shell-prompt marker in the prod tree that makes the branch state visible, document "the prod tree should always be on a tag" in the runbook, optionally `chmod a-w` the src/ subtree of the prod checkout (defense in depth).

- **[Risk]** Dev chatbot writes to prod's vLLM, exhausting GPU memory or queueing requests behind dev traffic so prod latency spikes. **Mitigation:** acknowledged, not solved here. Operator should avoid heavy dev workloads (large batch embedding, big model evaluations) on the live vLLM during business hours. A future change can add a separate dev vLLM or per-deploy GPU pinning.

- **[Risk]** Dev's postgres password is identical to prod's (config copy-paste). A dev chatbot misconfigured with the prod DB URL successfully connects and writes garbage. **Mitigation:** Decision 6 — different passwords between deploys.

- **[Risk]** The first prod redeploy without `--dev` rebuilds the chatbot image, which can fail if the Dockerfile or requirements don't currently build cleanly from the chosen tag. **Mitigation:** test the build on the dev deploy *first*, before retagging prod and redeploying.

- **[Risk]** Port 7891 conflicts with some future deploy or another service on the host. **Mitigation:** document the port allocation table in the runbook so the next deploy picks +40, not +30.

- **[Risk]** Staff hit dev expecting it to be stable and file bug reports against it. **Mitigation:** the dev chatbot UI gains a clearly visible banner ("DEV — branch HEAD, may be broken") before the iptables rule opens external access. Out of scope: building any kind of "is dev stable right now" status page.

- **[Trade-off]** Prod hotfix loop becomes ~10x slower than today. This is the cost of insulation and is intentional.

## Migration Plan

The migration has three phases. Each phase is independently verifiable; the operator should not start phase N+1 until phase N is observably good.

### Phase 1 — Stabilize prod (downtime: small, ~1-2 min container restart)

1. Pick the SHA to pin prod to. Easiest choice is the current tip of the `dev` branch at the time of cutover, after the in-flight feature branch (currently `feat/ghcr-dual-publish`) is merged.
2. Create a git tag (e.g., `v0.1.0-prod`) on that SHA on `dev`. Push the tag.
3. In `~/archi-openai-compat`: `git fetch && git switch --detach v0.1.0-prod`.
4. Run `archi deploy` (no `--dev`). This rebuilds `chatbot-archi-openai-compat` with code baked into the image and rewrites `compose.yaml` without the source bind mount.
5. Verify: `docker inspect chatbot-archi-openai-compat` — no `/home/a2rchi/archi-openai-compat/src` mount listed.
6. Verify: production URL still works, key user flow (chat → answer) succeeds.

**Rollback:** if step 4-6 fails, redeploy from the same tag with `--dev` to restore the previous bind-mount behavior. The actual user-visible behavior should be identical to what was running before.

### Phase 2 — Stand up dev (downtime: none for prod; new deploy)

1. `git clone <fork> /home/a2rchi/archi-openai-compat-dev && cd /home/a2rchi/archi-openai-compat-dev && git switch dev`.
2. Create `configs/config.dev.yaml` derived from prod's config: change `name` to `archi-openai-compat-dev`, change the chatbot/data-manager/grafana port numbers, change the postgres password.
3. Create the dev secrets directory at `~/.archi/archi-archi-openai-compat-dev/secrets/` and copy in the four API key files from prod.
4. `archi deploy --dev` from the dev tree, pointing at `configs/config.dev.yaml`.
5. Verify: four new containers running (`docker ps | grep -- -dev`).
6. Verify: dev chatbot UI loads from `localhost:7891` (test via SSH tunnel before opening the firewall).
7. Verify: dev chatbot can talk to vLLM (send a test message, get a response).

**Rollback:** `docker compose -f ~/.archi/archi-archi-openai-compat-dev/compose.yaml down -v` removes the dev deploy and its volumes. Prod is untouched throughout phase 2.

### Phase 3 — Open the firewall (downtime: none)

1. Add an iptables INPUT rule at position 12 allowing tcp/7891 from the intended source range (whatever range staff are on).
2. Verify from an off-host staff machine that `https://archi.rc.fas.harvard.edu:7891` (or `http://` depending on TLS termination) responds.
3. Add the "DEV" banner to the dev chatbot UI (or accept the visual difference of "weirdly different URL" as the marker).
4. Document the dev URL in the team's usual place (Slack channel topic, internal wiki — exact location is the operator's call).

**Rollback:** remove the iptables rule. Dev remains running and accessible to the operator via SSH tunnel.

## Open Questions

- **Q1:** Is the in-flight feature branch (`feat/ghcr-dual-publish`) safe to merge into `dev` before phase 1, or should phase 1 pin to the last known-good `dev` SHA prior to it? — Operator decides at cutover time.
- **Q2:** What is the staff source IP range for the iptables rule? — Needs an answer before phase 3 step 1.
- **Q3:** Does the existing prod chatbot Dockerfile build cleanly without `--dev`? — Should be true since `--dev` is purely additive to the rendered compose, but Phase 1 step 4 is the first time this is exercised on this host. Test in phase 2 first by running the dev deploy without `--dev` once, then turning it on.
- **Q4:** Should grafana be replicated for dev at all, or skipped to save resources? — Default: replicate, for parity. Can be removed from dev's compose if it proves wasteful.

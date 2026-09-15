## 1. Pre-cutover decisions

- [ ] 1.1 Resolve **Q1** from design: confirm whether `feat/ghcr-dual-publish` is merged into `dev` before cutover, or pick a `dev` SHA from before it
- [ ] 1.2 Resolve **Q2** from design: capture the staff source IP/CIDR range to allow through iptables for port 7891
- [ ] 1.3 Pick the prod git tag name (default suggestion: `v0.1.0-prod`); confirm with operator before tagging
- [ ] 1.4 Announce the planned ~1-2 min prod chatbot restart to anyone who would notice

## 2. Phase 1 — Stabilize production

- [ ] 2.1 In `~/archi-openai-compat`, `git fetch --tags origin`
- [ ] 2.2 Create the chosen tag on the chosen SHA: `git tag -a <tagname> -m "First pinned prod release" <sha>` and push it (`git push origin <tagname>`)
- [ ] 2.3 `git -C ~/archi-openai-compat switch --detach <tagname>` and verify HEAD is detached at the tag
- [ ] 2.4 Run `archi deploy` (no `--dev`) from the prod tree
- [ ] 2.5 Verify `~/.archi/archi-archi-openai-compat/compose.yaml` no longer contains a `src/:/root/archi/src` bind mount on any application service
- [ ] 2.6 Verify `docker inspect chatbot-archi-openai-compat` shows no host `src/` mount
- [ ] 2.7 Verify the production chatbot still answers a test message end-to-end
- [ ] 2.8 Touch a file under `~/archi-openai-compat/src/` (e.g., add a harmless comment), restart the prod chatbot, and confirm the change is **not** visible inside the container — proves the bind mount is gone
- [ ] 2.9 Revert the touch from 2.8 so the prod tree stays clean on the tag
- [ ] 2.10 If anything in 2.4–2.7 fails, redeploy from the same tag with `--dev` and stop to debug before continuing

## 3. Phase 2 — Stand up the dev deployment

- [ ] 3.1 `git clone <fork-url> /home/a2rchi/archi-openai-compat-dev` (or use the same upstream as the prod tree)
- [ ] 3.2 `cd /home/a2rchi/archi-openai-compat-dev && git switch dev && git pull`
- [ ] 3.3 Create `/home/a2rchi/archi-openai-compat-dev/configs/config.dev.yaml` by copying prod's `configs/config.yaml` and editing:
  - [ ] 3.3.1 set `name: archi-openai-compat-dev`
  - [ ] 3.3.2 change chatbot external port `7861` → `7891`
  - [ ] 3.3.3 change data-manager external port `7889` → `7919`
  - [ ] 3.3.4 change grafana external port `3000` → `3030`
  - [ ] 3.3.5 confirm OpenAI provider `base_url` is still `http://localhost:8000/v1` (shared vLLM)
- [ ] 3.4 Create the dev secrets directory at `~/.archi/archi-archi-openai-compat-dev/secrets/`
- [ ] 3.5 Copy `openai_api_key.txt`, `anthropic_api_key.txt`, `hf_token.txt`, and `grafana_pg_password.txt` from prod's secrets dir into the dev secrets dir
- [ ] 3.6 Generate a new postgres password and write it to `~/.archi/archi-archi-openai-compat-dev/secrets/pg_password.txt` — MUST differ from prod's
- [ ] 3.7 Verify no symlinks in the dev secrets dir (`find ~/.archi/archi-archi-openai-compat-dev/secrets -type l` returns empty)
- [ ] 3.8 From the dev tree, run `archi deploy --dev --config configs/config.dev.yaml` (and whatever `--hostmode` / `--services` flags prod uses)
- [ ] 3.9 Verify `docker ps` shows the four new `-dev`-suffixed containers all `Up`
- [ ] 3.10 Verify both deployments' container sets are listed and none overlap
- [ ] 3.11 Verify dev chatbot's compose has the src bind mount: `grep '/root/archi/src' ~/.archi/archi-archi-openai-compat-dev/compose.yaml`
- [ ] 3.12 Open an SSH tunnel to localhost:7891 from your workstation and confirm the dev chatbot UI loads
- [ ] 3.13 Send a test message through the dev chatbot, confirm vLLM responds
- [ ] 3.14 Verify dev's postgres is empty (or at least disjoint from prod's): connect and list user-data tables
- [ ] 3.15 Confirm prod is still healthy after dev startup (no port collisions, no GPU OOM)

## 4. Phase 3 — Open the firewall for staff

- [ ] 4.1 Insert iptables INPUT rule at position 12 to allow tcp/7891 from the staff source range (per host convention: `sudo iptables -I INPUT 12 -p tcp --dport 7891 -s <range> -j ACCEPT`)
- [ ] 4.2 Persist the iptables rule using whatever mechanism this host uses for other rules (verify by checking how port 7861 is persisted)
- [ ] 4.3 From an off-host staff machine, confirm `archi.rc.fas.harvard.edu:7891` is reachable and serves the dev UI
- [ ] 4.4 Verify the dev chatbot UI displays a visible "DEV" or "development" marker before any user input (if absent, file a follow-up and either add the marker or revert 4.1 until it exists)

## 5. Documentation

- [ ] 5.1 Add an operator note to `docs/docs/` describing: how prod is updated (tag → checkout → `archi deploy`), how dev is updated (`git pull` → `archi deploy --dev`), the port allocation table, and the dev URL
- [ ] 5.2 Document the iptables rule and the staff source range
- [ ] 5.3 Mark the prod tree visually (shell prompt, a `README.PROD` in the tree, or similar) so operators do not mistake which checkout they are editing
- [ ] 5.4 Add a one-line callout in the prod runbook: "Never run `git switch` in `~/archi-openai-compat` without intending a release"

## 6. Verification against specs

- [ ] 6.1 Walk every scenario in `specs/parallel-deployments/spec.md` against the running system and confirm each one passes
- [ ] 6.2 Capture any scenarios that don't pass cleanly as open issues (e.g., the "non-production marker" scenario likely needs UI work)

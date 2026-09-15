## Why

The only archi deployment on `holygpu7c0717.rc.fas.harvard.edu` today is the one users hit, and it is running with `--dev` — the chatbot container bind-mounts `/home/a2rchi/archi-openai-compat/src` live, so any `git switch` or in-place edit in that checkout immediately changes what production serves. There is no safe place on this server to test in-flight feature work without putting users at risk, and no defined notion of a "stable" version of the deployment to roll back to.

## What Changes

- **BREAKING (operationally):** the existing production deployment is redeployed *without* `--dev`. Its chatbot image is rebuilt with code baked in from a pinned git ref, so live host-FS edits no longer affect users. Hotfixes now require rebuild-and-redeploy, not file edits.
- A second archi deployment named `archi-openai-compat-dev` is created on the same host. It has its own container set, postgres, vectorstore, grafana, port allocation, and data volumes — **fully separated** from production.
- The dev deployment runs from a second checkout (`/home/a2rchi/archi-openai-compat-dev`) pinned to the `dev` branch, with `--dev` (live source bind-mount) enabled. Editing files in that tree and restarting the dev chatbot is the iteration loop.
- Both deployments share exactly one resource: the host-local vLLM server at `http://localhost:8000/v1` (acting as the OpenAI-compatible model backend).
- Staff can access the dev deployment via a second external port on `archi.rc.fas.harvard.edu` (e.g. `:7891`), opened with an explicit iptables rule. Production keeps its current port and URL.
- Updating dev to follow `dev`-branch HEAD is a documented manual loop (`git pull` + `archi deploy --dev`); no automation in this change.

## Capabilities

### New Capabilities
- `parallel-deployments`: how two archi deployments (a stable production and a live-edit development) coexist on a single host, what is shared (vLLM only) versus isolated (postgres, vectorstore, grafana, ports, containers, secrets paths), and the operational contract that protects production from accidental dev contamination.

### Modified Capabilities

_None._ The existing `dev-mode-mounts` and `cli-dev-mode` capabilities continue to describe what `--dev` does to a single deployment; this change adds rules about *which* deployment runs with `--dev` and how they are arranged side by side. No requirements in those capabilities change.

## Impact

- **Operational:** the runbook for shipping a production change becomes "merge to `dev`, pick a SHA, tag it, redeploy prod off that tag" — slower than today's edit-and-restart, intentionally.
- **Infrastructure:**
  - new container set: `chatbot-archi-openai-compat-dev`, `data-manager-archi-openai-compat-dev`, `postgres-archi-openai-compat-dev`, `grafana-archi-openai-compat-dev`
  - new docker volume(s) for dev's postgres + vectorstore data, under `/scratch/docker/volumes/`
  - new iptables INPUT rule at position 12 for the dev chatbot external port
  - a second working tree at `/home/a2rchi/archi-openai-compat-dev`
- **Code:** no archi CLI changes are required. Both `--dev` and non-`--dev` deploys are already supported (`cli/cli_main.py:57`).
- **CI:** no changes in this proposal. A follow-up could publish service images to GHCR so prod can pull instead of building locally, but that is out of scope.
- **Documentation:** `docs/docs/` gains a short operator note covering: how to update dev, how to ship a prod change, what to do if dev's port clashes with a future deploy.
- **Risk that warrants flagging:** the GPU is shared. Both prod and dev hit the same vLLM. Heavy dev traffic can slow prod inference. Out of scope to fix here, but operators should know.

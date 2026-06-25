# FASRC Archi — Deployment Notes

Operational notes specific to the Harvard **FASRC** deployment of archi (the
`fasrc/archi` fork). These cover host- and environment-specific details that do
not belong in the upstream docs — primarily the self-hosted vLLM model server
that backs the chat app.

Host: `archi.rc.fas.harvard.edu` (DNS alias for `holygpu7c0717.rc.fas.harvard.edu`),
4× Tesla V100-PCIE-32GB.

---

## Model server (vLLM / Qwen 3.6)

The chat app does **not** ship the LLM — it calls an OpenAI-compatible vLLM
server that runs separately on the GPU host.

| Property | Value |
|----------|-------|
| Model | `palmfuture/Qwen3.6-35B-A3B-GPTQ-Int4` |
| Endpoint | `http://localhost:8001/v1` |
| GPUs | 2,3 (`--tensor-parallel-size 2`) |
| Container image | `/scratch/a2rchi/sifs/vllm_volta.sif` (custom Volta build) |
| Runtime | Singularity / Apptainer (`singularity exec`) |
| Engine flags | TP=2, `--max-model-len 32768`, `--gpu-memory-utilization 0.88`, `--max-num-seqs 16`, `--enable-prefix-caching`, `--enable-auto-tool-choice --tool-call-parser qwen3_coder`, `--enable-log-requests`. **No** `--reasoning-parser` — see [Disabling model thinking](#disabling-model-thinking-enable_thinkingfalse). |
| Logs | journald (`sudo journalctl -u vllm-qwen36`) when run under systemd; `/scratch/a2rchi/vllm_instance2.log` **only** for the manual launcher |

The chat app's pointer to this endpoint is the authoritative **running config in
Postgres** (`static_config.services_config`, database `archi-db`), seeded at
`archi create` from `config/environments/dev.yaml`. Editing a `config.yaml` on
disk and restarting is a no-op; re-run the deploy (`g.sh`) to reseed. See
[Configuration](#configuration-where-the-running-config-lives).

> The server is **not** part of the docker-compose stack and is not started by
> `archi create`. It is launched independently as described below.

---

## Starting the model server manually

```bash
bash config/scripts/singularity_vllm_qwen36_volta.sh
```

This backgrounds the process and writes to `/scratch/a2rchi/vllm_instance2.log`.
Engine warmup takes **~2 minutes** (model load + torch.compile / CUDA-graph
capture); it is ready when the log shows `Application startup complete.` and
`GET /v1/models` returns HTTP 200. **Prefer the systemd service** (below) for the
production instance — the manual launcher has no auto-restart.

To stop it, target the specific PID — **never** a broad `pkill -f`, which on a
shared GPU cluster could hit other users' instances:

```bash
pgrep -f vllm.entrypoints.openai.api_server   # find the PID
kill <pid>
```

### The metrics-middleware compat shim (required)

`vllm_volta.sif` pairs `fastapi 0.137.2` with
`prometheus_fastapi_instrumentator 8.0.0`. FastAPI's newer `_IncludedRouter`
route object has no `.path` attribute,
but the older instrumentator reads `route.path` on every request — so it raises
`AttributeError` and returns **HTTP 500 on every request**, including
`/v1/chat/completions`. The engine loads fine; only the metrics middleware
breaks, and there is no vLLM flag to disable it.

Fix: `config/scripts/vllm_patch/sitecustomize.py` wraps `get_route_name` to
return `None` on `AttributeError` (the instrumentator then falls back to the raw
URL path). It is injected via `SINGULARITYENV_PYTHONPATH=/opt/vllm_patch` and a
bind mount, so Python auto-imports it at interpreter startup. **Do not** wrap the
entrypoint with a `runpy` launcher instead — that breaks vLLM's multiprocessing
`spawn` workers (`freeze_support()` RuntimeError); vLLM must stay the `__main__`
module. Both launch scripts already wire the shim in.

---

## Auto-start on reboot (systemd)

The user crontab is blocked by PAM on this host, so `@reboot` cron is not an
option. The host runs systemd, and we have `sudo`, so a **system-level systemd
service** is used. It also restarts the server on crash and logs to journald.

> **Status (2026-06-25): installed, `enabled`, and `active`.** The production
> server runs under this unit — `Restart=on-failure` recovers it after a crash
> (e.g. an OOM). The earlier note that it was "failed since 2026-06-18" no longer
> applies; that stale failure was just a port/GPU collision with a manual
> instance, cleared at cutover.

systemd needs a **foreground** process, so the service uses a foreground variant
of the launcher (no backgrounding, no log redirect):
`config/scripts/vllm_qwen36_volta_serve.sh`.

### Unit file

Source of truth: `config/scripts/vllm-qwen36.service`.

```ini
[Unit]
Description=archi Qwen3.6 vLLM server (V100, port 8001)
After=network-online.target
Wants=network-online.target
RequiresMountsFor=/scratch/a2rchi /home/a2rchi
StartLimitIntervalSec=600
StartLimitBurst=5

[Service]
Type=simple
User=a2rchi
Group=a2rchi
Environment=HOME=/home/a2rchi
TimeoutStartSec=600
ExecStart=/home/a2rchi/archi-openai-compat/config/scripts/vllm_qwen36_volta_serve.sh
Restart=on-failure
RestartSec=15

[Install]
WantedBy=multi-user.target
```

`RequiresMountsFor` makes the unit wait for the `/scratch` and `/home`
filesystems; `TimeoutStartSec=600` allows for model warmup.

### Install

```bash
# Stop any manually-launched instance first so the service can bind :8001.
# List the matching PID(s), then kill the specific one you intend to stop.
pgrep -af vllm.entrypoints.openai.api_server
kill <pid>   # replace <pid> with the PID printed above

sudo install -m 644 \
  /home/a2rchi/archi-openai-compat/config/scripts/vllm-qwen36.service \
  /etc/systemd/system/vllm-qwen36.service
sudo systemctl daemon-reload
sudo systemctl enable --now vllm-qwen36.service     # enable = start on boot; --now starts it immediately
```

### Operate

```bash
sudo systemctl status vllm-qwen36          # state
sudo systemctl start vllm-qwen36           # start
sudo systemctl stop vllm-qwen36            # stop
sudo systemctl restart vllm-qwen36         # restart
sudo journalctl -u vllm-qwen36 -f          # follow logs (watch for "Application startup complete.")
```

Under systemd the server logs to **journald**, not to
`/scratch/a2rchi/vllm_instance2.log` (that path is written **only** by the manual
launcher and is stale once the service is in charge). `journalctl` needs `sudo`
here (the `a2rchi` user is not in `systemd-journal`).

> **PID-matching gotcha at cutover.** The session/service user is `a2rchi`, but an
> interactive admin shell is typically `swinney`. `ss -ltnp` only reveals a
> socket's PID to the **owning** user, so a `swinney` shell trying to find the
> manual instance's PID on `:8001` gets nothing and won't kill it — then
> `systemctl start` launches a second server that collides on the port/GPUs. Stop
> the manual instance as `a2rchi` (or by reading the PID from `nvidia-smi
> --query-compute-apps`) **before** starting the service.

### Caveats

- The service **claims GPUs 2,3 on every boot**. This is correct only while the
  box is dedicated to archi; if those GPUs are ever SLURM-managed or shared,
  auto-claiming them on boot will collide.
- Run **exactly one** instance. Once the service is enabled, start/stop it with
  `systemctl` — do not also run `singularity_vllm_qwen36_volta.sh`, or the two
  servers will fight over port 8001 and the GPUs.

---

## GPU memory & the 0.88 utilization cap

`--gpu-memory-utilization` is set to **0.88**, not vLLM's default. On a 31.73 GiB
V100, vLLM pre-reserves its KV-cache pool at startup, but ~2–2.7 GiB of non-torch
memory (CUDA context, NCCL, cuBLAS/MoE workspaces) lives **outside** that budget.
At 0.95 each worker idled at ~31.5 GiB — only a few hundred MiB below the ceiling
— so a single long-context prefill (the Qwen 3.6 GatedDeltaNet FLA kernel
allocates transient activations) tipped it over: **`CUDA out of memory` →
`EngineCore` dies → the whole server exits** (a dead EngineCore is fatal in vLLM
v1). The crash followed ~16 h of idle, so it looked random; it was really the
first request large enough to exceed the thin headroom — not concurrency.

0.88 leaves ~4.5 GiB headroom (idle ~28.2 GiB), absorbing worst-case prefill
spikes; the KV pool is still ~15 GiB / ~400k tokens, far more than this workload
uses. `--enable-log-requests` was added so the next such event records the
triggering request's shape.

Do **not** add `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` on this image —
it crashes CUDA-graph capture (`VllmWorker died in compile_or_warm_up_model`) and
only ever reclaimed ~135 MiB of fragmentation anyway.

---

## Disabling model thinking (`enable_thinking=false`)

Qwen 3.6 is a reasoning model; archi runs it with thinking **off** for latency.
The switch is in the chat-app provider config (seeded to Postgres):

```yaml
services:
  chat_app:
    providers:
      openai:
        extra_kwargs:
          extra_body:
            chat_template_kwargs:
              enable_thinking: false
```

(in `config/environments/dev.yaml`). It is forwarded to vLLM as
`chat_template_kwargs={"enable_thinking": false}`; the model then answers directly
with no `<think>` trace.

**Critical: the launch must NOT pass `--reasoning-parser qwen3`.** This image's
custom qwen3 parser fixes `prompt_has_open_think=True` at startup, so when a
no-think reply contains no `</think>` it routes the entire answer into the
discarded reasoning channel — **every reply comes back with empty `content`**.
(Symptom: raw `/v1/completions` returns the answer, but `/v1/chat/completions`
returns `""`.) The parser only matters when thinking is *on*; with thinking off it
is pure harm, so it is omitted from both launch scripts. Tool-calling is
unaffected — `--tool-call-parser qwen3_coder` is independent.

To change this end-to-end: edit the config + reseed (`g.sh`), or confirm Postgres
already has it, then **(1)** `sudo systemctl restart vllm-qwen36` (server picks up
any flag change), then **(2)** `docker restart chatbot-archi-openai-compat` (app
reloads config from Postgres). Server first, app second — the reverse order
briefly serves empty answers.

---

## Configuration: where the running config lives

Two layers:

- **Source file (edit this):** `config/environments/dev.yaml`. The active deploy
  is the **repo-root** `./g.sh`:
  `archi create --name archi-openai-compat --dev --config ./config/environments/dev.yaml --services chatbot,grafana --hostmode`.
  (A *separate* `config/scripts/g.sh` deploys an unrelated `main-gpu-agent` from
  `config/vllm-config.yaml` — not the chat app described here.)
- **Authoritative running config (what archi reads):** Postgres
  `static_config.services_config` (db `archi-db`, container
  `postgres-archi-openai-compat`), seeded from `dev.yaml` at `archi create`.

Consequence: **editing `dev.yaml` + restarting is a no-op** — re-run `g.sh` to
reseed Postgres. A `docker restart chatbot-archi-openai-compat` only picks up
values already seeded into Postgres. `config/` is untracked (local deployment
state).

---

## File reference

| Path | Purpose |
|------|---------|
| `config/scripts/singularity_vllm_qwen36_volta.sh` | Manual launcher (backgrounds, logs to file) |
| `config/scripts/vllm_qwen36_volta_serve.sh` | Foreground launcher used by systemd; keep its engine flags identical to the manual launcher |
| `config/scripts/vllm_patch/sitecustomize.py` | prometheus/fastapi 500 compat shim |
| `config/scripts/vllm-qwen36.service` | systemd unit — **installed & active** at `/etc/systemd/system/vllm-qwen36.service` |
| `config/environments/dev.yaml` | Chat-app config seeded to Postgres (model, provider, `enable_thinking`) |
| `g.sh` (repo root) | Active deploy: `archi create` of chatbot + grafana from `dev.yaml` |
| `config/scripts/g.sh` | Separate `main-gpu-agent` deploy from `config/vllm-config.yaml` (not the chat app here) |

> `config/` is intentionally untracked in this repo (local deployment state), so
> these scripts live outside version control — keep this doc in sync by hand when
> they change.

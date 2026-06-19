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
| Log (manual launch) | `/scratch/a2rchi/vllm_instance2.log` |

The chat app's pointer to this endpoint is the authoritative **running config in
Postgres** (`static_config.services_config`, database `archi-db`), seeded at
`archi create` from `config/environments/dev.yaml`. Editing a `config.yaml` on
disk and restarting is a no-op; re-run the deploy (`g.sh`) to reseed.

> The server is **not** part of the docker-compose stack and is not started by
> `archi create`. It is launched independently as described below.

---

## Starting the model server manually

```bash
bash config/scripts/singularity_vllm_qwen36_volta.sh
```

This backgrounds the process and writes to `/scratch/a2rchi/vllm_instance2.log`.
Engine warmup takes ~50–60 s; it is ready when the log shows
`Application startup complete.` and `GET /v1/models` returns HTTP 200.

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
journalctl -u vllm-qwen36 -f               # follow logs (watch for "Application startup complete.")
```

### Caveats

- The service **claims GPUs 2,3 on every boot**. This is correct only while the
  box is dedicated to archi; if those GPUs are ever SLURM-managed or shared,
  auto-claiming them on boot will collide.
- Run **exactly one** instance. Once the service is enabled, start/stop it with
  `systemctl` — do not also run `singularity_vllm_qwen36_volta.sh`, or the two
  servers will fight over port 8001 and the GPUs.

---

## File reference

| Path | Purpose |
|------|---------|
| `config/scripts/singularity_vllm_qwen36_volta.sh` | Manual launcher (backgrounds, logs to file) |
| `config/scripts/vllm_qwen36_volta_serve.sh` | Foreground launcher used by systemd |
| `config/scripts/vllm_patch/sitecustomize.py` | prometheus/fastapi 500 compat shim |
| `config/scripts/vllm-qwen36.service` | systemd unit (install to `/etc/systemd/system/`) |
| `config/scripts/g.sh` / `g.sh` | `archi create` redeploy of chatbot + grafana |

> `config/` is intentionally untracked in this repo (local deployment state), so
> these scripts live outside version control — keep this doc in sync by hand when
> they change.

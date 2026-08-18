# FASRC Archi — Deployment Notes

Operational notes specific to the Harvard **FASRC** deployment of archi (the
`fasrc/archi` fork). These cover host- and environment-specific details that do
not belong in the upstream docs — primarily the self-hosted vLLM model server
that backs the chat app.

Host: `archi.rc.fas.harvard.edu` (DNS alias for `holygpu7c0717.rc.fas.harvard.edu`),
4× Tesla V100-PCIE-32GB.

**Two vLLM servers run on this host**, one per GPU pair, both under systemd:

| Model | Port | GPUs | systemd unit | archi provider slot |
|-------|------|------|--------------|---------------------|
| `palmfuture/Qwen3.6-35B-A3B-GPTQ-Int4` | 8001 | 2,3 | `vllm-qwen36.service` | `local` (default) |
| `palmfuture/Qwen3.8-27B-GPTQ-Int4` | 8002 | 0,1 | `vllm-qwen38.service` | `openai` (see [Running two models](#running-two-models-in-archi-provider-slots)) |

Qwen 3.8 requires two source patches to the container's vLLM — without them it
either crashes at load or silently emits garbage. See
[vLLM patches](#vllm-patches-required).

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
> the manual instance as `a2rchi` (or read the PID from
> `nvidia-smi --query-compute-apps=pid,process_name,used_gpu_memory --format=csv,noheader`)
> **before** starting the service.

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

To change this end-to-end, restart the **server first**: `g.sh` (the reseed/redeploy
below) recreates the chatbot, so reseeding before the server is corrected brings a
fresh chatbot up against the **old** vLLM and serves empty answers in the window.
Order: **(1)** edit the launch script + `sudo systemctl restart vllm-qwen36` (server
picks up the flag change — wait for `GET /v1/models` HTTP 200), then **(2)** edit
`config/environments/dev.yaml` + re-run `g.sh` to reseed Postgres and redeploy the
chatbot (or, if only the server changed and Postgres already has the right config,
just `docker restart chatbot-archi-openai-compat`). Server first, app second — the
reverse order briefly serves empty answers.

---

## Second model server (vLLM / Qwen 3.8)

| Property | Value |
|----------|-------|
| Model | `palmfuture/Qwen3.8-27B-GPTQ-Int4` |
| Endpoint | `http://localhost:8002/v1` |
| GPUs | 0,1 (`--tensor-parallel-size 2`) |
| Container image | `/scratch/a2rchi/sifs/vllm_volta.sif` (same image as 3.6) |
| Engine flags | Same as 3.6 except `--gpu-memory-utilization 0.80` (GPU 0 is shared with the data-manager embedding worker) |
| Logs | journald (`sudo journalctl -u vllm-qwen38`); `/scratch/a2rchi/vllm.log` **only** for the manual launcher |
| Patches | **Required** — two bind-mounts, see below |

Port 8002 rather than 8000: **8000 is already taken** by the RAGAS Dataset
Manager on this host.

```bash
bash config/scripts/singularity_vllm_qwen38_volta.sh   # manual (backgrounds)
sudo systemctl status vllm-qwen38.service              # systemd (installed & active)
```

Install/operate exactly as for 3.6 — stop any manual instance first so the unit
can bind :8002, and kill by specific PID, never a broad `pkill -f vllm`.

**Both models are hybrid linear-attention architectures**, with the same 3:1
layer pattern (`full_attention_interval: 4`) — 3.8 is 64 layers (48
`linear_attention` + 16 `full_attention`), 3.6 is 40 (30 + 10). The patches are
**not** explained by an architecture difference: 3.6 runs the same Gated DeltaNet
path, which is why the GPU-memory section above attributes its prefill spikes to
the FLA kernel.

What actually differs is the **GPTQ quantization layout**. Both checkpoints
record their exclusions in `quantization_config.dynamic`, but at very different
granularity:

| | 3.6 (`Qwen3_5MoeForConditionalGeneration`) | 3.8 (`Qwen3_5ForConditionalGeneration`) |
|---|---|---|
| linear-attn exclusions | `-:.*attn.*` — one regex, excludes the **entire** attention stack | seven per-parameter regexes (`in_proj_a`, `in_proj_b`, `in_proj_ba`, `A_log`, `conv1d`, `dt_bias`, `norm`) |
| `in_proj_qkv` / `in_proj_z` | **not** quantized — swept up by the broad regex | **int4** |
| net effect | nothing mixed-precision inside the GDN block | int4 and bf16 weights **side by side** in one block |

So 3.6 never exercises the fork's fused-vs-split projection logic and loads
cleanly unpatched; 3.8 does, and that is the source of both patches below.

---

## vLLM patches (required)

`config/scripts/vllm_patches/qwen3_5.py` is bind-mounted **over** the copy inside
the `.sif` at launch. A file-over-file `--bind` is the mechanism — `PYTHONPATH`
cannot shadow a module that lives inside the installed `vllm` package. Both fixes
are in that one file; its `README.md` carries the full write-up.

**Do not try to fix these by upgrading vLLM.** The image reports version `1.1.0`,
but upstream's newest release is **v0.27.1** — this is a Volta (SM 7.0) fork
build (hence the companion `flash_attn_v100` wheel and `FLASH_ATTN_V100`
backend). Its `qwen3_5.py` is 1099 lines against upstream's 732, and the broken
code **does not exist upstream**, so there is no fix to pull. There is also no
build recipe on disk: `vllm_volta.def` copies in pre-built wheels,
`/scratch/src/vllm-volta` is only a conda env, and the build commands have aged
out of shell history. Rebuilding means re-deriving the Volta port from scratch.

### Fix 1 — load-time crash

```
qwen3_5.py:571 -> AttributeError: 'RowvLLMParameter' object has no attribute 'output_dim'
```

The tuple-shard branch of `load_weights` assumes every parameter in the fused GDN
projection is split along an output dimension. GPTQ's `g_idx` is indexed per
*input* channel and deliberately has no `output_dim`. Fix: load params lacking
`output_dim` whole, unsharded. Guarding only the `logger.debug` line is not
enough — the same unguarded access recurs in the bounds check and `narrow()`.

### Fix 2 — silent garbage output

Symptom: server returns HTTP 200, but every response is token id 0 (`!`) repeated
to `max_tokens`, and a `logprobs` request 500s because NaN will not serialize.
The tell-tale is a warning that is easy to miss among the startup noise, logged
96 times (48 GDN layers × 2 TP workers):

```
WARNING [qwen3_5.py:676] Parameter layers.N.linear_attn.in_proj_qkvz.weight
                         not found in params_dict, skip loading
```

The checkpoint is **mixed precision** inside the linear-attention block:
`in_proj_qkv`/`in_proj_z` are GPTQ int4 while `in_proj_b`/`in_proj_a` stay bf16.
The fork handles this via `_uses_split_gdn_input_projections()`, but that function
was written for **AWQ** and reads only `modules_to_not_convert`/`ignored_layers` —
both `None` on a GPTQ checkpoint, which records exclusions in `dynamic` under
`-:`-prefixed regexes. It therefore returned `False`, the model built one fused
*quantized* projection, and the bf16 b/a weights were dropped with only a warning,
leaving every DeltaNet gate on uninitialized memory. Fix: also read GPTQ's
`dynamic` exclusions.

**Verify after any change to this file:**

```bash
# Scope to the CURRENT invocation. A bare `journalctl -u vllm-qwen38` replays
# retained history, so the 96 warnings from the bad launch keep matching after
# the patch is fixed -- the check would then report failure permanently.
INV=$(systemctl show -p InvocationID --value vllm-qwen38)

# must print 0 -- anything else means weights are being silently dropped
sudo journalctl _SYSTEMD_INVOCATION_ID="$INV" | grep -c "not found in params_dict"
# must answer "Paris" and "391", not "!!!!"
curl -s http://localhost:8002/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"palmfuture/Qwen3.8-27B-GPTQ-Int4","messages":[{"role":"user",
       "content":"Capital of France? Then 17*23. Two short lines."}],"max_tokens":80,"temperature":0}'
```

Ruled out during diagnosis, recorded so nobody re-treads them: the `g_idx` patch
is **not** a cause of garbage (both checkpoints set `desc_act: false`, so `g_idx`
is trivially sequential); the `4-bit gptq_gemm kernel is buggy` warning is a **red
herring** (3.6 logs it too and is fine — it is emitted unconditionally at
config-parse time); and it is **not** an fp16/bf16 issue (GPTQ in this build
accepts *only* fp16 — `--dtype float32` is rejected outright).

---

## Running two models in archi (provider slots)

archi's providers are a **closed enum** — `openai`, `anthropic`, `gemini`,
`openrouter`, `local`, `cern_litellm`, `huit_bedrock`. Provider config is read by
`_build_provider_config_from_payload()` (`chat_app/app.py:155`, the enum lookup
at `:162`), so an invented
key like `local2:` is **not rejected — it is silently never read**. Each provider
type gets exactly one `base_url`, so two vLLM endpoints cannot both be `local`.

Because vLLM speaks the OpenAI API and `OpenAIProvider` honors a custom
`base_url` (`openai_provider.py:147`), the **`openai` slot is repurposed** for the
second endpoint:

```yaml
openai:
  enabled: true
  base_url: http://localhost:8002/v1
  default_model: "palmfuture/Qwen3.8-27B-GPTQ-Int4"
  models: ["palmfuture/Qwen3.8-27B-GPTQ-Int4"]
  extra_kwargs:
    api_key: "sk-noauth"          # see below
    extra_body:
      chat_template_kwargs:
        enable_thinking: false    # 3.8 emits <think> tags
```

Two non-obvious requirements:

1. **A placeholder `api_key` in `extra_kwargs`.** A bare vLLM needs no auth, but
   the OpenAI SDK refuses to construct a client with no key at all. There is no
   `api_key` field in the provider YAML schema; `extra_kwargs` is spread into
   `ChatOpenAI(**kwargs)`, so the placeholder lands correctly.
2. **`OPENAI_API_KEY=sk-noauth` in the secrets env too.** Without it
   `is_configured()` is `False` (`providers/base.py:127` — only `local` is exempt
   from needing a key), so `/api/providers` reports `enabled: false` and the UI
   **hides the model** (`static/chat.js:1572`). The endpoint still works via
   direct API call, which makes this easy to misdiagnose.

> **Never put a real OpenAI key in this env file** while this slot points at a
> local endpoint: `openai_provider.py:144` lets the env value override the
> placeholder, so it would be transmitted to `localhost:8002`. This provider slot
> and genuine OpenAI usage are mutually exclusive on this deployment. Note also
> that `extra_kwargs` is persisted verbatim into Postgres `static_config`, so a
> real credential must never live there.

### The override is streaming-only

Per-request model selection (`"provider": "openai", "model": "..."` in the chat
payload) is implemented in `stream()` (`app.py:1984`); the override block begins
at `:2111` (`_create_provider_llm`) and logs `Serving request from request-local
view with <provider>/<model>` at `:2139`. The non-streaming
`/api/get_chat_response` does parse `provider`/`model` — in
`_parse_chat_request()` (`app.py:4742`, the two keys at `:4768-4769`) — and then
**ignores them**, silently serving the default provider and still returning
**HTTP 200**.

Verify an override against `/api/get_chat_response_stream`, and confirm at the
destination — the target vLLM's request log — never by HTTP 200 alone:

```bash
# same invocation scoping as above -- retained history would inflate the count
INV=$(systemctl show -p InvocationID --value vllm-qwen38)
sudo journalctl _SYSTEMD_INVOCATION_ID="$INV" | grep -c "Received request"
# must increase across the request under test
```

---

## Embedding device: why it must be `cpu`

`data_manager.embedding_class_map.HuggingFaceEmbeddings.kwargs.model_kwargs.device`
is consumed by **both** containers — the data-manager *and* the chatbot, which
also builds a `VectorStoreManager` for retrieval. Setting `cuda:0` crash-loops the
chatbot:

```
manager.py:103 -> HuggingFaceEmbeddings(device="cuda:0")
AssertionError: Torch not compiled with CUDA enabled
```

There is **no cuda-availability fallback anywhere in the codebase** — the device
string is passed through verbatim.

**This is latent and detonates on re-seed.** Because the running config lives in
Postgres, the value can sit in the config file un-deployed for days; an unrelated
redeploy is what finally applies it. So a deploy can break the chatbot with a
change you did not make. After any deploy, check
`docker inspect chatbot-archi-openai-compat --format '{{.RestartCount}}'` — not
just HTTP 200 on the UI, which the crash loop can still briefly satisfy.

**There is no per-service GPU setting.** The compose template picks GPU images
with one global flag applied to every service:

```jinja
dockerfile: .../Dockerfile-chat{{ '-gpu' if gpu_ids else '' }}
```

The rendered compose already names `Dockerfile-chat-gpu` for the chatbot, but the
running chatbot image is a **stale CPU build**:

| | chatbot | data-manager |
|---|---|---|
| torch | `2.6.0+cpu` | `2.6.0+cu124` |
| `/usr/local/cuda` | absent | present |
| image size | 4.46 GB | 18.9 GB |

**`--force` is not the reason.** `archi create --force` only overwrites an
existing deployment directory; the compose step that follows defaults to
`--build --force-recreate --always-recreate-deps`
(`DeploymentManager.start_deployment()`,
`src/cli/managers/deployment_manager.py:48-51`), so an ordinary deploy *does*
rebuild images. A stale image therefore means either no deploy has run since
`gpu_ids` was set, or the ambient `ARCHI_COMPOSE_UP_FLAGS` was overridden and
dropped `--build` — CI does exactly that
(`.github/workflows/test-and-build-tag.yml:161`). Verify the image, never infer
it from the flag:

```bash
docker inspect chatbot-archi-openai-compat --format '{{.Image}}'
```

So enabling `cuda:0` here still means **an actual chatbot image rebuild**, not
just editing config. Think before doing so: the chatbot's reservation is `count: all`, so a
CUDA chatbot claims GPUs 0–3 — including the pair running vLLM, which pre-reserves
its KV pool at startup, making it a startup-order race. The chatbot only embeds
short queries, not the bulk ingest that the GPU data-manager work actually sped
up. Keep `device: cpu` while vLLM owns the GPUs.

---

## Configuration: where the running config lives

Two layers:

- **Source file (edit this):** `config/environments/dev.yaml`. The active deploy
  is the **repo-root** `./g.sh`:
  `archi create --name archi-openai-compat --dev --config ./config/environments/dev.yaml --services chatbot,grafana --hostmode --force`.
  The `--force` (`-f`) is **required** on a reseed: `archi create` aborts with
  `Deployment '...' already exists` otherwise (it overwrites the existing
  deployment dir; volumes are preserved). A *separate* `config/scripts/g.sh`
  deploys an unrelated `main-gpu-agent` from `config/vllm-config.yaml` — not the
  chat app described here.
- **Authoritative running config (what archi reads):** Postgres
  `static_config.services_config` (db `archi-db`, container
  `postgres-archi-openai-compat`), seeded from `dev.yaml` at `archi create`.

Consequence: **editing `dev.yaml` + restarting is a no-op** — re-run `g.sh` to
reseed Postgres. A `docker restart chatbot-archi-openai-compat` only picks up
values already seeded into Postgres. `config/` is a checkout of the separate
`fasrc/archi-config` repo — see the note at the end of
[File reference](#file-reference).

> **This is not the `deploy/fasrc-dev/` deployment.** That one is
> `DEPLOYMENT="dev"` (`deploy/fasrc-dev/scripts/lib.sh:14-16`) → containers
> `chatbot-dev` / `postgres-dev`, and it runs on a host with **no GPUs**, pointing
> at a remote vLLM endpoint (`lib.sh:21-29`). Everything on this page is the
> `archi-openai-compat` deployment on `archi.rc.fas.harvard.edu`. The container
> names are not interchangeable between the two.

---

## File reference

| Path | Purpose |
|------|---------|
| `config/scripts/singularity_vllm_qwen36_volta.sh` | Qwen 3.6 manual launcher (backgrounds, logs to file) |
| `config/scripts/vllm_qwen36_volta_serve.sh` | Qwen 3.6 foreground launcher used by systemd; keep its engine flags identical to the manual launcher |
| `config/scripts/vllm-qwen36.service` | systemd unit — **installed & active** at `/etc/systemd/system/vllm-qwen36.service` |
| `config/scripts/singularity_vllm_qwen38_volta.sh` | Qwen 3.8 manual launcher (:8002, GPUs 0,1) |
| `config/scripts/vllm_qwen38_volta_serve.sh` | Qwen 3.8 foreground launcher used by systemd; keep flags identical to the manual launcher |
| `config/scripts/vllm-qwen38.service` | systemd unit — **installed & active** at `/etc/systemd/system/vllm-qwen38.service` |
| `config/scripts/vllm_patch/sitecustomize.py` | prometheus/fastapi 500 compat shim (both servers) |
| `config/scripts/vllm_patches/qwen3_5.py` | Qwen 3.8 weight-loading patches — **required**, see [vLLM patches](#vllm-patches-required) |
| `config/scripts/vllm_patches/README.md` | Full write-up of both patches, with the ruled-out hypotheses |
| `config/environments/dev.yaml` | Chat-app config seeded to Postgres (model, provider, `enable_thinking`) |
| `g.sh` (repo root) | Active deploy: `archi create` of chatbot + grafana from `dev.yaml` |
| `config/scripts/g.sh` | Separate `main-gpu-agent` deploy from `config/vllm-config.yaml` (not the chat app here) |

> **`config/` is a separate repository — but none of these vLLM files are in it.**
> `config/` is a checkout of **`fasrc/archi-config`** (private), provisioned on
> every deploy at a pinned, SHA-verified ref by `ensure_config` (`.gitignore:40-44`,
> `deploy/fasrc-dev/scripts/lib.sh:4-6,32-45`). It is git-ignored *in this repo*
> only because committing it would break as a gitlink or leak
> `config/benchmarking/secrets.env` — not because it is unversioned. Recovery
> edits belong in that repo and its pin, not in an ignored local checkout.
>
> The gap is narrower than "untracked", and worse for being invisible: as of the
> `deploy-pin-2026-08a` pin, `git ls-files` inside `config/` returns
> `scripts/prune-build-cache.sh` and `scripts/systemd/**` only — **no launcher, no
> systemd unit, no `vllm_patch/` or `vllm_patches/`**. Of the table above, only
> `config/environments/dev.yaml` is actually tracked. So `config/` being a git
> checkout buys these files nothing. In August 2026 `vllm_patch/sitecustomize.py`
> and `vllm_qwen36_volta_serve.sh` were found **missing from disk** while
> `vllm-qwen36.service` was still `enabled` and `active`: the running 3.6 server
> survived only because it was already up, and `Restart=on-failure` or a reboot
> would have left production down with no way to start it. Both had to be
> reconstructed by hand — a `git checkout` in `config/` would not have restored
> them.
>
> **The fix is to commit them to `fasrc/archi-config`, not to maintain this page
> as the record.** Add the launchers, both units, the compat shim and
> `vllm_patches/` there, then bump the pin: create a *new* annotated tag in
> `fasrc/archi-config` (never move an existing one — `git fetch --tags` refuses to
> clobber a moved tag) and update `CONFIG_REF` + `CONFIG_SHA` in
> `deploy/fasrc-dev/scripts/lib.sh` in the same PR (`lib.sh:32-45`). Until that
> lands, this page plus `vllm_patches/README.md` are the interim record and must be
> kept in sync by hand.

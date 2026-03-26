# vLLM Provider

Run open-weight models on your own GPUs using [vLLM](https://docs.vllm.ai/) as an inference backend. Archi connects to any vLLM server via its OpenAI-compatible API — you deploy and manage vLLM independently.

## Why vLLM?

| | vLLM | Ollama | API providers |
|---|---|---|---|
| **Throughput** | High (PagedAttention, continuous batching) | Moderate | N/A (cloud) |
| **Multi-GPU** | Tensor-parallel across GPUs | Single GPU | N/A |
| **Tool calling** | Supported (with parser flag) | Model-dependent | Supported |
| **Cost** | Hardware only | Hardware only | Per-token |
| **Privacy** | Data stays on-premises | Data stays on-premises | Data leaves your network |

vLLM is the best fit when you need high-throughput local inference, multi-GPU support, or full data privacy with tool-calling capabilities.

## Architecture

```
┌──────────────────────┐         ┌──────────────────────┐
│   archi deployment    │         │  vLLM (external)     │
│                      │         │                      │
│  ┌────────────────┐  │  HTTP   │  Docker container    │
│  │ VLLMProvider   │──│────────>│  OR bare metal       │
│  │ (Python client)│  │  :8000  │  OR Slurm job        │
│  └────────────────┘  │  /v1/*  │  OR Kubernetes pod   │
│                      │         │                      │
└──────────────────────┘         └──────────────────────┘
```

Archi's `VLLMProvider` is a thin client that talks to vLLM's `/v1` API using the same `ChatOpenAI` LangChain class it would use for the OpenAI API. From the pipeline's perspective, vLLM looks identical to a remote OpenAI endpoint.

**Archi does not manage the vLLM server.** You deploy, configure, and maintain vLLM independently — whether as a Docker container, a bare metal process, a Slurm job, or a Kubernetes pod. Archi only needs a `base_url` to connect.

## Quick Start

### 1. Start a vLLM server

See [Running vLLM](#running-vllm) below for Docker, bare metal, and Slurm examples.

### 2. Configure archi

In your config YAML, set up the vLLM provider with the URL of your server:

```yaml
services:
  chat_app:
    default_provider: vllm
    default_model: "vllm:Qwen/Qwen3-8B"
    providers:
      vllm:
        enabled: true
        base_url: http://localhost:8000/v1  # URL of your vLLM server
        default_model: "Qwen/Qwen3-8B"
        models:
          - "vllm:Qwen/Qwen3-8B"
```

### 3. Deploy archi

```bash
archi create -n my-deployment \
  -c config.yaml \
  -e .env \
  --services chatbot
```

### 4. Verify

```bash
# Check vLLM is serving
curl http://localhost:8000/v1/models

# Check archi can reach it
curl http://localhost:7861/api/health
```

## Configuration Reference

### Provider settings

The vLLM provider is configured under `services.chat_app.providers.vllm`:

```yaml
services:
  chat_app:
    providers:
      vllm:
        enabled: true
        base_url: http://localhost:8000/v1
        default_model: "Qwen/Qwen3-8B"
        models:
          - "vllm:Qwen/Qwen3-8B"
```

| Setting | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `false` | Enable the vLLM provider |
| `base_url` | string | `http://localhost:8000/v1` | vLLM server OpenAI-compatible endpoint |
| `default_model` | string | — | HuggingFace model ID to use for inference |
| `models` | list | — | Available model IDs for the UI model selector |

### Model references

Anywhere a model is referenced in `pipeline_map`, use the `vllm/` prefix:

```yaml
archi:
  pipeline_map:
    CMSCompOpsAgent:
      models:
        required:
          agent_model: vllm/Qwen/Qwen3-8B
```

The part after `vllm/` must match the HuggingFace model ID that vLLM is serving.

> **Model naming**: vLLM uses HuggingFace model IDs (e.g. `Qwen/Qwen3-8B`), not Ollama-style names (e.g. `Qwen/Qwen3:8B`).

## Running vLLM

Archi does not manage the vLLM server. Below are examples for common deployment scenarios.

### Docker

```bash
docker run -d \
  --name vllm-server \
  --gpus all \
  --ipc=host \
  --ulimit memlock=-1 \
  --ulimit stack=67108864 \
  -p 8000:8000 \
  -e NCCL_P2P_DISABLE=1 \
  vllm/vllm-openai:latest \
  --model Qwen/Qwen3-8B \
  --enable-auto-tool-choice \
  --tool-call-parser hermes
```

Key flags:
- `--gpus all` — GPU passthrough
- `--ipc=host` — required for NCCL multi-GPU communication (Docker's default 64MB shm causes crashes)
- `--ulimit memlock=-1` — prevents OS from swapping out VRAM-mapped buffers
- `NCCL_P2P_DISABLE=1` — required for V100s and older GPU topologies

### Bare metal

```bash
pip install vllm

python -m vllm.entrypoints.openai.api_server \
  --model Qwen/Qwen3-8B \
  --enable-auto-tool-choice \
  --tool-call-parser hermes \
  --host 0.0.0.0 \
  --port 8000
```

### Slurm

```bash
#!/bin/bash
#SBATCH --gres=gpu:4
#SBATCH --cpus-per-task=16
#SBATCH --mem=128G
#SBATCH --time=7-00:00:00

module load cuda
source activate vllm

python -m vllm.entrypoints.openai.api_server \
  --model Qwen/Qwen3-8B \
  --tensor-parallel-size 4 \
  --enable-auto-tool-choice \
  --tool-call-parser hermes \
  --host 0.0.0.0 \
  --port 8000
```

Then set `base_url` in your archi config to the Slurm node's address.

### Common vLLM server flags

These are configured on the vLLM server itself, not in archi:

| Flag | Description |
|---|---|
| `--gpu-memory-utilization 0.9` | Fraction of GPU VRAM to use (0.0-1.0) |
| `--max-model-len 8192` | Cap context window to reduce memory |
| `--tensor-parallel-size 4` | Shard model across N GPUs |
| `--dtype bfloat16` | Force weight precision |
| `--quantization awq` | Run quantized weights (awq, gptq, fp8) |
| `--enforce-eager` | Disable CUDA graphs to save memory |
| `--max-num-seqs 256` | Limit concurrent sequences |
| `--enable-auto-tool-choice` | Enable tool calling pathway |
| `--tool-call-parser hermes` | Parser for structured tool calls |

See [vLLM documentation](https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html) for the full reference.

## Tool Calling

vLLM supports function/tool calling for ReAct agents, but requires explicit server flags:

- `--enable-auto-tool-choice` — enables the tool calling pathway
- `--tool-call-parser <parser>` — selects the parser for the model family

| Model family | Parser |
|---|---|
| Qwen (Qwen2.5, Qwen3) | `hermes` |
| Mistral / Mixtral | `mistral` |
| Llama 3 | `llama3_json` |

These flags must be set when starting the vLLM server, not in archi's config.

## Troubleshooting

### Archi can't reach vLLM

**Symptom**: `ConnectionError: Connection refused` or timeout.

- Verify vLLM is running: `curl http://<vllm-host>:8000/v1/models`
- If vLLM is on a different host, ensure network connectivity and firewall rules allow port 8000
- If running in Docker, ensure the archi container can reach the vLLM host (use `--network=host` or configure Docker networking)
- Check that `base_url` in your archi config matches the actual vLLM server address

### Model not found (404)

**Symptom**: `Error: model 'Qwen/Qwen3:8B' does not exist`.

vLLM uses HuggingFace model IDs, not Ollama-style names. Check:

- Config uses the exact model ID from `curl <vllm-host>:8000/v1/models`
- Use dashes, not colons: `Qwen/Qwen3-8B` (not `Qwen/Qwen3:8B`)

### Tool calling returns 400

**Symptom**: `400 Bad Request: "auto" tool choice requires --enable-auto-tool-choice`.

The vLLM server wasn't started with tool calling flags. Add to your vLLM launch command:

```bash
--enable-auto-tool-choice --tool-call-parser hermes
```

### Slow first response

The first request after startup may be slow (30-60s) while vLLM compiles CUDA kernels. Subsequent requests will be significantly faster. If this is a problem, start vLLM with `--enforce-eager` to skip CUDA graph compilation (at the cost of lower throughput).

### Insufficient VRAM

If vLLM crashes or the model doesn't fit in GPU memory:

- Lower `--gpu-memory-utilization` (e.g. `0.7`)
- Set `--max-model-len` to a smaller value (e.g. `4096`)
- Add `--quantization awq` or `--quantization gptq` if quantized weights are available
- Set `--enforce-eager` to disable CUDA graphs
- Increase `--tensor-parallel-size` and use more GPUs
- Try a smaller model

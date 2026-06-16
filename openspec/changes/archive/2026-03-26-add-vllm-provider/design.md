# Design: vLLM Provider Integration

## Architecture

Archi connects to vLLM as an external service. The operator is responsible for deploying, configuring, and maintaining the vLLM server.

```
┌──────────────────────┐         ┌──────────────────────┐
│   archi deployment    │         │  vLLM (external)     │
│                      │         │                      │
│  ┌────────────────┐  │  HTTP   │  Docker container    │
│  │ VLLMProvider   │──│────────▶│  OR bare metal       │
│  │ (Python client)│  │  :8000  │  OR Slurm job        │
│  └────────────────┘  │  /v1/*  │  OR Kubernetes pod   │
│                      │         │                      │
└──────────────────────┘         └──────────────────────┘
```

- **Client:** `VLLMProvider` in archi's chatbot container.
- **Server:** Any vLLM instance exposing the OpenAI-compatible API.
- **Protocol:** HTTP/REST (OpenAI schema) at `{base_url}/v1/`.

## Technical Decisions

### Decision: Inherit from BaseProvider, not OpenAIProvider
vLLM is OpenAI-compatible at the API level, but `VLLMProvider` inherits from `BaseProvider` to avoid coupling to OpenAI's default model list, API key validation, and pricing logic. Uses `ChatOpenAI` from LangChain with a custom `base_url`.

### Decision: No infrastructure management
Archi does not manage the vLLM server lifecycle. This means:
- No vllm-server service in Docker Compose
- No engine arg passthrough in config (max_model_len, tensor_parallel_size, etc.)
- No GPU configuration in archi's templates_manager
- No health checks or depends_on for vLLM in the compose stack

The operator provides a `base_url` and archi connects to it. If vLLM is down, the provider's `validate_connection()` reports the failure.

### Decision: Minimal config surface
The only config keys archi needs:
- `enabled` — whether the provider is active
- `base_url` — where to reach the vLLM API (default: `http://localhost:8000/v1`)
- `default_model` — which model to use for inference
- `models` — optional list of available models (can also be discovered via `/v1/models`)

### Decision: API key defaults to "not-needed"
vLLM's OpenAI-compatible API does not require authentication by default. The provider sets `api_key="not-needed"` to satisfy the `ChatOpenAI` client's required parameter. If the operator adds auth to vLLM, they can set the key in config.

## Operator Responsibilities (documented, not enforced)
The vLLM documentation in archi should describe but not automate:
- Docker flags for GPU inference (`--gpus all`, `--ipc=host`, `--ulimit memlock=-1`)
- NCCL configuration for multi-GPU setups (`NCCL_P2P_DISABLE=1` for V100s)
- Model selection and context window sizing (`--max-model-len`)
- Network accessibility (vLLM must be reachable from archi's chatbot container)

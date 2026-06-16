# Proposal: Add vLLM Provider

## Intent
Enable archi to use vLLM as an LLM provider for high-throughput local inference. The provider connects to any vLLM instance via its OpenAI-compatible API — regardless of how or where that instance is deployed (Docker, bare metal, Slurm, Kubernetes).

## Scope
- New provider class `VLLMProvider` in `src/archi/providers/`.
- Integration into the provider factory.
- Support for streaming and non-streaming chat completions.
- Documentation for connecting archi to an external vLLM server.
- Unit tests for the provider client.

## Explicitly Out of Scope
- **vLLM server lifecycle management.** Archi does not start, stop, configure, or monitor the vLLM server. The operator manages vLLM infrastructure independently.
- **Docker Compose service for vllm-server.** No `vllm-server` container in archi's compose stack.
- **GPU configuration passthrough.** Engine args (`max_model_len`, `tensor_parallel_size`, etc.) are the operator's responsibility, not archi config keys.
- **Smoke tests that assume in-stack vLLM.** Unit tests mock the API; integration testing against a live vLLM server is the operator's concern.

## Rationale for Scope Change
The original proposal included a managed vLLM container within archi's Docker Compose stack. This was revised based on real-world usage:

- One deployment runs vLLM in a Docker container alongside archi.
- Another deployment runs vLLM on bare metal at a different university.
- Both connect to archi identically — via `base_url`.

Managing GPU infrastructure (NCCL flags, shared memory, tensor parallelism, model loading) is specialized operational work that varies significantly between environments. Archi's role is to **connect to** LLM providers, not **manage** them. This aligns with the broader principle that archi connects to infrastructure (PostgreSQL, Grafana, LLM servers) rather than owning it.

## Constraints
- MUST use OpenAI-compatible API format.
- MUST support the `base_url` parameter for connecting to any vLLM instance.
- MUST NOT require vLLM to be co-deployed with archi.

## Configuration
```yaml
providers:
  vllm:
    enabled: true
    default_model: "Qwen/Qwen3-8B"
    base_url: "http://gpu-node:8000/v1"
```

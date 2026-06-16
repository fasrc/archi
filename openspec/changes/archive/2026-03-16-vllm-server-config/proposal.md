## Why

The vLLM server container currently launches with a hardcoded command that only accepts model name and tool-call parser. Operators have no way to tune GPU memory utilization, context length, parallelism, quantization, or any other vLLM engine argument without manually editing the rendered compose file — which gets overwritten on the next `archi create`. This blocks production GPU deployments where memory budgeting and multi-GPU distribution are mandatory.

## What Changes

- Expose commonly-used vLLM engine arguments as named config keys under `services.chat_app.providers.vllm` (gpu_memory_utilization, max_model_len, tensor_parallel_size, dtype, quantization, enforce_eager, max_num_seqs, enable_prefix_caching).
- Add a generic `engine_args` dict for any vLLM flag not covered by a named key, rendered as `--key value` pairs on the launch command.
- Update the compose template to conditionally append each flag to the `vllm.entrypoints.openai.api_server` command.
- Pass new config values through `templates_manager.py` into compose template variables.
- Update the `basic-vllm` example config to demonstrate GPU memory and parallelism settings.
- Document vLLM server tuning in the deployment guide.

## Capabilities

### New Capabilities
- `vllm-server-config`: Configurable vLLM server launch arguments via archi YAML config, covering GPU memory, parallelism, quantization, context length, and arbitrary engine args passthrough.

### Modified Capabilities

## Impact

- **`src/cli/templates/base-compose.yaml`** — vLLM server command section gains conditional Jinja2 blocks for each supported flag.
- **`src/cli/managers/templates_manager.py`** — reads new config keys from `services.chat_app.providers.vllm` and passes them as template vars.
- **`examples/deployments/basic-vllm/config.yaml`** — updated with example GPU/parallelism settings.
- **`docs/`** — new or updated section on vLLM server tuning.
- No changes to `VLLMProvider` Python class (it's a client; server config is compose-side).
- No breaking changes. All new keys are optional with sensible defaults (vLLM's own defaults apply when omitted).

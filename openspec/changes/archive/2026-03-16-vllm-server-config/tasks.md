## 1. templates_manager.py — read and pass config

- [x] 1.1 Read named vLLM config keys (gpu_memory_utilization, max_model_len, tensor_parallel_size, dtype, quantization, enforce_eager, max_num_seqs, enable_prefix_caching) from `services.chat_app.providers.vllm` and set corresponding `vllm_<name>` template vars
- [x] 1.2 Read `engine_args` dict from vllm config and pass as `vllm_engine_args` template var (default to empty dict)

## 2. base-compose.yaml — render flags in launch command

- [x] 2.1 Replace single-line vLLM launch command (line 580) with multi-line command using conditional `{% if %}` blocks for each named flag
- [x] 2.2 Add `{% for %}` loop over `vllm_engine_args` to append arbitrary `--key value` pairs
- [x] 2.3 Handle boolean flags: `enforce_eager` true → `--enforce-eager`; `enable_prefix_caching` false → `--no-enable-prefix-caching`
- [x] 2.4 Handle empty-string values in engine_args as bare `--flag` (no argument)

## 3. Example config and docs

- [x] 3.1 Update `examples/deployments/basic-vllm/config.yaml` with commented examples for gpu_memory_utilization, tensor_parallel_size, max_model_len, and engine_args
- [x] 3.2 Add vLLM server tuning section to deployment docs listing all named keys, their types/defaults, and the engine_args passthrough

## 4. Testing

- [x] 4.1 Verify `archi create` with minimal vllm config renders launch command with only model + tool-parser (no regression)
- [x] 4.2 Verify `archi create` with all named keys renders each `--flag value` in the launch command
- [x] 4.3 Verify `archi create` with engine_args renders passthrough flags
- [x] 4.4 Verify boolean flag edge cases: enforce_eager false → omitted, enable_prefix_caching false → `--no-enable-prefix-caching`

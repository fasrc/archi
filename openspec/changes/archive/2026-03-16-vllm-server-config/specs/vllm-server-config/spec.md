## ADDED Requirements

### Requirement: GPU memory utilization is configurable
The system SHALL accept an optional `gpu_memory_utilization` key (float, 0.0–1.0) under `services.chat_app.providers.vllm` and render it as `--gpu-memory-utilization <value>` in the vLLM server launch command. When omitted, the flag SHALL NOT appear (vLLM default 0.9 applies).

#### Scenario: Operator sets gpu_memory_utilization
- **WHEN** config contains `services.chat_app.providers.vllm.gpu_memory_utilization: 0.7`
- **THEN** the rendered compose command includes `--gpu-memory-utilization 0.7`

#### Scenario: Operator omits gpu_memory_utilization
- **WHEN** config does not contain `gpu_memory_utilization` under the vllm provider
- **THEN** the rendered compose command does NOT include `--gpu-memory-utilization`

### Requirement: Max model length is configurable
The system SHALL accept an optional `max_model_len` key (integer) under `services.chat_app.providers.vllm` and render it as `--max-model-len <value>` in the vLLM server launch command. When omitted, the flag SHALL NOT appear (vLLM uses the model's default context length).

#### Scenario: Operator sets max_model_len
- **WHEN** config contains `services.chat_app.providers.vllm.max_model_len: 8192`
- **THEN** the rendered compose command includes `--max-model-len 8192`

#### Scenario: Operator omits max_model_len
- **WHEN** config does not contain `max_model_len` under the vllm provider
- **THEN** the rendered compose command does NOT include `--max-model-len`

### Requirement: Tensor parallel size is configurable
The system SHALL accept an optional `tensor_parallel_size` key (integer, >= 1) under `services.chat_app.providers.vllm` and render it as `--tensor-parallel-size <value>` in the vLLM server launch command. When omitted, the flag SHALL NOT appear (vLLM default 1 applies).

#### Scenario: Operator sets tensor_parallel_size for multi-GPU
- **WHEN** config contains `services.chat_app.providers.vllm.tensor_parallel_size: 4`
- **THEN** the rendered compose command includes `--tensor-parallel-size 4`

#### Scenario: Operator omits tensor_parallel_size
- **WHEN** config does not contain `tensor_parallel_size` under the vllm provider
- **THEN** the rendered compose command does NOT include `--tensor-parallel-size`

### Requirement: Dtype is configurable
The system SHALL accept an optional `dtype` key (string: auto, float16, bfloat16, float32) under `services.chat_app.providers.vllm` and render it as `--dtype <value>` in the vLLM server launch command. When omitted, the flag SHALL NOT appear (vLLM default "auto" applies).

#### Scenario: Operator sets dtype
- **WHEN** config contains `services.chat_app.providers.vllm.dtype: bfloat16`
- **THEN** the rendered compose command includes `--dtype bfloat16`

#### Scenario: Operator omits dtype
- **WHEN** config does not contain `dtype` under the vllm provider
- **THEN** the rendered compose command does NOT include `--dtype`

### Requirement: Quantization method is configurable
The system SHALL accept an optional `quantization` key (string: awq, gptq, fp8, bitsandbytes) under `services.chat_app.providers.vllm` and render it as `--quantization <value>` in the vLLM server launch command. When omitted, the flag SHALL NOT appear (no quantization applied).

#### Scenario: Operator sets quantization
- **WHEN** config contains `services.chat_app.providers.vllm.quantization: awq`
- **THEN** the rendered compose command includes `--quantization awq`

#### Scenario: Operator omits quantization
- **WHEN** config does not contain `quantization` under the vllm provider
- **THEN** the rendered compose command does NOT include `--quantization`

### Requirement: Enforce eager mode is configurable
The system SHALL accept an optional `enforce_eager` key (boolean) under `services.chat_app.providers.vllm`. When `true`, the system SHALL render `--enforce-eager` in the launch command. When `false` or omitted, the flag SHALL NOT appear.

#### Scenario: Operator enables enforce_eager
- **WHEN** config contains `services.chat_app.providers.vllm.enforce_eager: true`
- **THEN** the rendered compose command includes `--enforce-eager`

#### Scenario: Operator sets enforce_eager to false
- **WHEN** config contains `services.chat_app.providers.vllm.enforce_eager: false`
- **THEN** the rendered compose command does NOT include `--enforce-eager`

#### Scenario: Operator omits enforce_eager
- **WHEN** config does not contain `enforce_eager` under the vllm provider
- **THEN** the rendered compose command does NOT include `--enforce-eager`

### Requirement: Max number of sequences is configurable
The system SHALL accept an optional `max_num_seqs` key (integer) under `services.chat_app.providers.vllm` and render it as `--max-num-seqs <value>` in the vLLM server launch command. When omitted, the flag SHALL NOT appear (vLLM default 256 applies).

#### Scenario: Operator sets max_num_seqs
- **WHEN** config contains `services.chat_app.providers.vllm.max_num_seqs: 64`
- **THEN** the rendered compose command includes `--max-num-seqs 64`

#### Scenario: Operator omits max_num_seqs
- **WHEN** config does not contain `max_num_seqs` under the vllm provider
- **THEN** the rendered compose command does NOT include `--max-num-seqs`

### Requirement: Prefix caching is configurable
The system SHALL accept an optional `enable_prefix_caching` key (boolean) under `services.chat_app.providers.vllm`. When `false`, the system SHALL render `--no-enable-prefix-caching` in the launch command. When `true` or omitted, the flag SHALL NOT appear (vLLM default is prefix caching enabled).

#### Scenario: Operator disables prefix caching
- **WHEN** config contains `services.chat_app.providers.vllm.enable_prefix_caching: false`
- **THEN** the rendered compose command includes `--no-enable-prefix-caching`

#### Scenario: Operator enables prefix caching explicitly
- **WHEN** config contains `services.chat_app.providers.vllm.enable_prefix_caching: true`
- **THEN** the rendered compose command does NOT include `--no-enable-prefix-caching` or `--enable-prefix-caching`

#### Scenario: Operator omits enable_prefix_caching
- **WHEN** config does not contain `enable_prefix_caching` under the vllm provider
- **THEN** the rendered compose command does NOT include any prefix caching flag

### Requirement: Arbitrary engine args passthrough
The system SHALL accept an optional `engine_args` dict under `services.chat_app.providers.vllm`. Each key-value pair SHALL be rendered as `--<key> <value>` appended to the vLLM server launch command. Keys SHALL be used verbatim (kebab-case, matching vLLM CLI flag names). A value of empty string SHALL render as `--<key>` with no argument (for boolean flags).

#### Scenario: Operator passes additional engine args
- **WHEN** config contains `services.chat_app.providers.vllm.engine_args: { swap-space: 8, seed: 42 }`
- **THEN** the rendered compose command includes `--swap-space 8` and `--seed 42`

#### Scenario: Operator passes a boolean passthrough flag
- **WHEN** config contains `services.chat_app.providers.vllm.engine_args: { trust-remote-code: "" }`
- **THEN** the rendered compose command includes `--trust-remote-code`

#### Scenario: Operator omits engine_args
- **WHEN** config does not contain `engine_args` under the vllm provider
- **THEN** no additional flags are appended beyond the named keys

#### Scenario: engine_args is empty dict
- **WHEN** config contains `services.chat_app.providers.vllm.engine_args: {}`
- **THEN** no additional flags are appended beyond the named keys

### Requirement: All config keys are optional with vLLM defaults
All vLLM server config keys (named and engine_args) SHALL be optional. When a key is omitted from config, the corresponding CLI flag SHALL NOT be rendered in the launch command. vLLM's own built-in defaults SHALL apply for any omitted flag.

#### Scenario: Minimal vllm config with only required keys
- **WHEN** config contains only `services.chat_app.providers.vllm: { enabled: true, default_model: "Qwen/Qwen3-8B" }`
- **THEN** the rendered launch command contains only `--model`, `--enable-auto-tool-choice`, and `--tool-call-parser` (existing behavior preserved)

#### Scenario: Fully specified vllm config
- **WHEN** config contains all named keys and engine_args
- **THEN** the rendered launch command includes all corresponding flags in order: model, tool-choice, tool-parser, named flags, engine_args

### Requirement: Config values pass through templates_manager
The `templates_manager.py` SHALL read vLLM server config keys from `services.chat_app.providers.vllm` and pass them as template variables to the compose template. Named keys SHALL be prefixed with `vllm_` (e.g., `gpu_memory_utilization` → `vllm_gpu_memory_utilization`). The `engine_args` dict SHALL be passed as `vllm_engine_args` (defaulting to empty dict when absent).

#### Scenario: templates_manager passes named keys
- **WHEN** config contains `services.chat_app.providers.vllm.tensor_parallel_size: 2`
- **THEN** `templates_manager.py` sets `template_vars["vllm_tensor_parallel_size"] = 2`

#### Scenario: templates_manager passes engine_args
- **WHEN** config contains `services.chat_app.providers.vllm.engine_args: { seed: 42 }`
- **THEN** `templates_manager.py` sets `template_vars["vllm_engine_args"] = { "seed": 42 }`

#### Scenario: templates_manager defaults engine_args to empty dict
- **WHEN** config does not contain `engine_args` under the vllm provider
- **THEN** `templates_manager.py` sets `template_vars["vllm_engine_args"] = {}`

### Requirement: Example config demonstrates server tuning
The `examples/deployments/basic-vllm/config.yaml` SHALL include commented examples of GPU memory utilization, tensor parallel size, and engine_args to document the available options.

#### Scenario: Example config contains commented vLLM server args
- **WHEN** an operator reads `examples/deployments/basic-vllm/config.yaml`
- **THEN** the file contains commented-out examples for `gpu_memory_utilization`, `tensor_parallel_size`, and `engine_args` with inline descriptions

## Context

The vLLM server container is defined in `base-compose.yaml` (line 561–621). Its launch command is a single shell line that runs `python3 -m vllm.entrypoints.openai.api_server` with only `--model` and `--tool-call-parser`. Config values reach the template through `templates_manager.py` (lines 429–434), which currently reads only `default_model` and `tool_call_parser` from `services.chat_app.providers.vllm`.

vLLM exposes dozens of engine arguments. The most operationally relevant ones from the docs:

| Flag | Type | Default | Purpose |
|---|---|---|---|
| `--gpu-memory-utilization` | float | 0.9 | Fraction of GPU VRAM for KV cache + model |
| `--max-model-len` | int | model default | Cap context window (reduces memory) |
| `--tensor-parallel-size` | int | 1 | Shard model across N GPUs |
| `--dtype` | str | auto | Weight precision (float16, bfloat16, auto) |
| `--quantization` | str | None | Quantization method (awq, gptq, fp8) |
| `--enforce-eager` | bool | false | Disable CUDA graphs (saves memory) |
| `--max-num-seqs` | int | 256 | Max concurrent sequences |
| `--enable-prefix-caching` | bool | true | KV cache prefix sharing |
| `--swap-space` | float | 4 | CPU swap space per GPU in GiB |
| `--data-parallel-size` | int | 1 | Replicate model across GPU groups |

## Goals / Non-Goals

**Goals:**
- Let operators configure common vLLM engine arguments via `services.chat_app.providers.vllm` config keys.
- Provide an escape-hatch `engine_args` dict for any vLLM flag not explicitly named.
- Keep all new keys optional — omitted keys are simply not passed, so vLLM's own defaults apply.
- Validate types at template-rendering time (float for gpu_memory_utilization, int for tensor_parallel_size, etc.).

**Non-Goals:**
- Runtime reconfiguration of vLLM (requires server restart; out of scope).
- Exposing vLLM flags that are already controlled by other archi mechanisms (e.g., `--model` comes from `default_model`, `--tool-call-parser` from `tool_call_parser`).
- Modifying `VLLMProvider` (Python client class) — these are server-side launch args only.
- Supporting per-model engine arg overrides (one vLLM server = one model).
- Docker GPU device selection (already handled by `gpu_ids` in compose template).

## Decisions

### 1. Named keys + `engine_args` passthrough

**Choice**: Expose ~10 named config keys for common flags, plus a freeform `engine_args` dict.

**Why over alternatives:**
- *Pure passthrough only*: No validation, easy to mistype flag names, poor discoverability. Operators would need to know vLLM CLI flags.
- *Named keys only*: Would require a code change every time a new vLLM flag is needed.
- *Hybrid*: Named keys give discoverability and type safety for the common cases; `engine_args` covers the long tail without code changes.

**Config shape:**
```yaml
services:
  chat_app:
    providers:
      vllm:
        enabled: true
        base_url: http://localhost:8000/v1
        default_model: "Qwen/Qwen3-8B"
        tool_call_parser: hermes
        # Named engine args (all optional):
        gpu_memory_utilization: 0.8
        max_model_len: 8192
        tensor_parallel_size: 2
        dtype: float16
        quantization: awq
        enforce_eager: false
        max_num_seqs: 128
        enable_prefix_caching: true
        # Passthrough for any other vLLM flag:
        engine_args:
          swap-space: 8
          data-parallel-size: 2
          seed: 42
```

### 2. Jinja2 rendering strategy

**Choice**: Build the full command as a multi-line shell string in the compose template using conditional `{% if %}` blocks for named args, plus a `{% for %}` loop over `engine_args`.

**Rationale**: This follows the existing pattern for `vllm_model` and `vllm_tool_parser`. The alternative — building the command in Python and passing it as a single template var — would move logic out of the template where it's harder to inspect in the rendered `compose.yaml`.

**Template sketch** (line 580 replacement):
```jinja2
exec python3 -m vllm.entrypoints.openai.api_server \
  --model "{{ vllm_model | default('Qwen/Qwen2.5-7B-Instruct-1M') }}" \
  --enable-auto-tool-choice \
  --tool-call-parser "{{ vllm_tool_parser | default('hermes') }}" \
  {% if vllm_gpu_memory_utilization is defined %}--gpu-memory-utilization {{ vllm_gpu_memory_utilization }} {% endif %}\
  {% if vllm_max_model_len is defined %}--max-model-len {{ vllm_max_model_len }} {% endif %}\
  {% if vllm_tensor_parallel_size is defined %}--tensor-parallel-size {{ vllm_tensor_parallel_size }} {% endif %}\
  {% if vllm_dtype is defined %}--dtype {{ vllm_dtype }} {% endif %}\
  {% if vllm_quantization is defined %}--quantization {{ vllm_quantization }} {% endif %}\
  {% if vllm_enforce_eager %}--enforce-eager {% endif %}\
  {% if vllm_max_num_seqs is defined %}--max-num-seqs {{ vllm_max_num_seqs }} {% endif %}\
  {% if vllm_enable_prefix_caching is defined %}{% if not vllm_enable_prefix_caching %}--no-enable-prefix-caching {% endif %}{% endif %}\
  {% for key, val in vllm_engine_args.items() %}--{{ key }} {{ val }} {% endfor %}
```

### 3. templates_manager.py passthrough

**Choice**: Read the full `vllm` config dict once, map named keys to `vllm_<name>` template vars, and pass `engine_args` as `vllm_engine_args`.

**Why**: Keeps the existing pattern (lines 429–434) and avoids a new abstraction. Each named key is an explicit `if cfg.get(...)` check, so typos in config are silently ignored (matching current behavior for `default_model`).

### 4. Boolean flag handling

**Choice**: Boolean flags use vLLM's `--flag` / `--no-flag` convention.

- `enforce_eager: true` → `--enforce-eager`
- `enforce_eager: false` or omitted → nothing appended
- `enable_prefix_caching: false` → `--no-enable-prefix-caching` (vLLM defaults to true)
- `enable_prefix_caching: true` or omitted → nothing appended (vLLM default is already true)

### 5. `engine_args` key format

**Choice**: Keys in `engine_args` use CLI-style kebab-case (`swap-space`, not `swap_space`), rendered verbatim as `--{{ key }} {{ val }}`.

**Why**: Operators can copy flag names directly from vLLM docs. No translation layer needed. Boolean passthrough flags use value `""` (empty string) to render as `--flag` with no argument.

## Risks / Trade-offs

- **Invalid flag values** → vLLM server fails to start. Mitigation: the healthcheck (retries 20, start_period 120s) will surface the failure. Named keys get basic type comments in the example config. Full validation is a non-goal — vLLM itself provides clear error messages.
- **`engine_args` conflicts with named keys** → If an operator sets both `gpu_memory_utilization: 0.8` and `engine_args: { gpu-memory-utilization: 0.7 }`, the flag appears twice. Mitigation: document that named keys take precedence and `engine_args` should only be used for flags without a named key.
- **vLLM version drift** → Flag names may change across vLLM releases. Mitigation: the `vllm/vllm-openai:latest` image tag already pins to a release. Named keys map 1:1 to stable, long-lived flags. `engine_args` handles new flags without code changes.
- **Multi-line command readability** → The Jinja2 command block becomes longer. Mitigation: each conditional is a single line, and rendered output is still a single `exec` command. Comments in the template explain each block.

## Phase 1: Provider (thin client layer)

- [x] 1. Add `ProviderType.VLLM` to enum in `src/archi/providers/base.py`
- [x] 2. Create `VLLMProvider` class in `src/archi/providers/vllm_provider.py` — inherits `BaseProvider`, default `base_url` `http://localhost:8000/v1`, `api_key` defaults to `"not-needed"`, `get_chat_model()` returns `ChatOpenAI` with correct `base_url`, `list_models()` hits `/v1/models`, `validate_connection()` health-checks `/v1/models`
- [x] 3. Register provider in `src/archi/providers/__init__.py` — add to `_ensure_providers_registered()`, point `"vllm"` alias to `ProviderType.VLLM`
- [x] 4. Config schema support — support `archi.providers.vllm` section in YAML (fields: `enabled`, `base_url`, `default_model`, `models`)

## Phase 2: Remove infrastructure management

- [x] 5. Remove vllm-server service definition from `src/cli/templates/base-compose.yaml`
- [x] 6. Remove vllm-server entry from `src/cli/service_registry.py`
- [x] 7. Remove vLLM engine arg passthrough from `src/cli/managers/templates_manager.py`
- [x] 8. Remove vLLM handling from `src/cli/utils/service_builder.py`
- [x] 9. Remove `examples/deployments/basic-gpu/config.yaml` (GPU compose config)
- [x] 10. Simplify `examples/deployments/basic-vllm/config.yaml` — remove engine args, keep only `base_url` + `default_model`
- [x] 11. Remove vLLM smoke tests (`tests/smoke/vllm_smoke.py`, vllm portions of `tests/smoke/combined_smoke.sh`)

## Phase 3: Remove unrelated changes

- [x] 12. Remove retriever.py URL fix (already on `fix/retriever-url-citations` branch)
- [x] 13. Remove `.gitignore` change (already merged separately)
- [x] 14. Remove `chat_app/app.py` change (if vLLM-unrelated)

## Phase 4: Documentation

- [x] 15. Rewrite `docs/docs/vllm.md` — "how to connect archi to a vLLM server" not "how to deploy vLLM with archi". Include operator guidance for Docker, bare metal, and Slurm setups.
- [x] 16. Update `docs/docs/user_guide.md` — remove vLLM server management references

## Phase 5: Validation

- [x] 17. Unit tests for VLLMProvider — mock `/v1/models`, verify `ChatOpenAI` instantiation, verify `validate_connection()` paths

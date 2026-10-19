## 1. Thread-count helper

- [x] 1.1 Write failing tests for `resolve_torch_threads`: default 8 when the key or the `data_manager` section is absent; a configured positive int is returned; 0, -1, True, 8.0 and "8" raise `ValueError` naming `data_manager.torch_threads`
- [x] 1.2 Implement `src/cli/utils/torch_threads.py` to pass them

## 2. Compose rendering

- [x] 2.1 Write failing render tests: `OMP_NUM_THREADS` is "8" or "4" in the `data-manager` and `chatbot` environments; it is absent from other services; it is absent when `torch_threads` is not supplied
- [x] 2.2 Add the conditional `OMP_NUM_THREADS` line to both service blocks in `base-compose.yaml`
- [x] 2.3 Write a failing test that `_render_compose_file` writes the configured value, and refuses an invalid one; then pass `torch_threads` from it

## 3. Verify

- [x] 3.1 `bash scripts/gate.sh` green
- [x] 3.2 Redeploy dev; both containers report `torch.get_num_threads() == 8`; chat retrieval spans drop from the 8.78 s baseline; chat smoke test returns HTTP 200

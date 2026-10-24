## 1. Thread-count helper

- [x] 1.1 Write failing tests for `resolve_torch_threads`: default 8 when the key or the `data_manager` section is absent; a configured positive int is returned; 0, -1, True, 8.0 and "8" raise `ValueError` naming `data_manager.torch_threads`
- [x] 1.2 Implement `src/cli/utils/torch_threads.py` to pass them

## 2. Compose rendering

- [x] 2.1 Write failing render tests: `OMP_NUM_THREADS` is "8" or "4" in the `data-manager` and `chatbot` environments; it is absent from other services; it is absent when `torch_threads` is not supplied
- [x] 2.2 Add the conditional `OMP_NUM_THREADS` line to both service blocks in `base-compose.yaml`
- [x] 2.3 Write a failing test that `_render_compose_file` writes the configured value, and refuses an invalid one; then pass `torch_threads` from it

## 3. Verify

- [x] 3.1 `bash scripts/gate.sh` green
- [x] 3.2 Redeploy dev from the branch at `6da1f8e4` (before group 4); both containers report `torch.get_num_threads() == 8`; chat retrieval spans drop from the 8.78 s baseline; chat smoke test returns HTTP 200
- [x] 3.3 Claw, without a redeploy: throwaway `docker run --rm --network none` containers from the current `data-manager-claw:2000` and `chatbot-claw:2000` images report 8 threads with the rendered `OMP_NUM_THREADS=8` (and, after 4.5, `MKL_NUM_THREADS=8`) and 16 (one per CPU) without them
- [ ] 3.4 After merge, redeploy dev from the `dev` tip on the GPU host and repeat the 3.2 checks. Tracked on [fasrc/archi#721](https://github.com/fasrc/archi/issues/721), which closes when this passes

## 4. Review fixes (PR #722)

- [x] 4.1 Write failing tests, then cap the default at the visible CPUs (`os.sched_getaffinity` when present, else `os.cpu_count`); an explicit `torch_threads` is not capped
- [x] 4.2 Write failing tests, then keep an `OMP_NUM_THREADS` from the deployment's `.env` when `torch_threads` is absent: compose renders no entry that overrides it
- [x] 4.3 Write failing tests, then render `torch_threads` into the deployed config when set, so `archi restart -c` refuses a change to it as a `data_manager` change
- [x] 4.4 Write failing tests, then check `torch_threads` in `validate_configs`, so `archi create` and `archi restart -c` refuse an invalid value before they write a file
- [x] 4.5 Write failing tests, then render `MKL_NUM_THREADS` with the same value as `OMP_NUM_THREADS` (torch reads it first), and keep the env file's values when it sets either variable

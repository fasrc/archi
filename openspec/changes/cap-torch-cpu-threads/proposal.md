## Why

The data-manager and chatbot containers run PyTorch with its default of one CPU thread per visible core, and nothing sets `OMP_NUM_THREADS`. On the 32-core dev host, shared with vLLM and about ten other containers, each MiniLM embedding call costs ~2,960 ms instead of ~16 ms. A full ingest's embed stage takes 61.6 min instead of 2.1 min, and every chat vector search spends 8.25–8.99 s in retrieval (fasrc/archi#721).

## What Changes

- Render `OMP_NUM_THREADS` into the `data-manager` and `chatbot` compose services, so torch starts a bounded thread pool.
- Add an optional config key `data_manager.torch_threads`: a positive integer, default 8, capped at the CPUs the host gives `archi create`. Without the key, an `OMP_NUM_THREADS` in the env file is kept.
- Reject a non-positive or non-integer value at `archi create` and `archi restart -c` time, naming the key, before any file is written.
- `archi restart -c` refuses a change to the key, as it refuses other `data_manager` changes.
- No change to any other service, the embedding model, device, chunking or batch sizes.

## Capabilities

### New Capabilities
- `container-cpu-threads`: the CPU thread count that archi's text-embedding containers run torch with, and how an operator sets it.

### Modified Capabilities
<!-- none -->

## Impact

- `src/cli/templates/base-compose.yaml`: the data-manager and chatbot `environment` blocks.
- `src/cli/managers/templates_manager.py`: `_render_compose_file` passes the resolved value to the template.
- New helper `src/cli/utils/torch_threads.py`, with unit tests.
- `src/cli/managers/config_manager.py`: `validate_configs` checks the key.
- `src/cli/templates/base-config.yaml`: records the key in the deployed config when it is set.
- Deployments pick it up on the next `archi create` (dev: `deploy/scripts/redeploy.sh`). No migration and no data change.

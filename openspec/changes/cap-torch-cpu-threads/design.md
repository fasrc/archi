## Context

PyTorch sizes its intra-op thread pool from `OMP_NUM_THREADS`, or from the visible core count when the variable is unset. archi's containers have no CPU limit, so each sees all 32 host cores. Measured on holygpu7c0717 (fasrc/archi#721):

| setting under test | 1 passage | embed stage, full ingest |
|---|---|---|
| `torch threads: 32` (default) | 2,962 ms | 61.6 min |
| `torch threads: 32 → 8` | 16 ms | 2.1 min |

Live chat retrieval (`search_vectorstore_hybrid`) at the default measured a median of 8.78 s over three turns.

## Goals / Non-Goals

**Goals:**
- Bound torch's CPU threads in the two containers that embed text: data-manager (ingest) and chatbot (query embedding).
- A sane default that needs no config change, plus an operator override.

**Non-Goals:**
- ONNX Runtime's reranker threads (FlashRank). It ignores `OMP_NUM_THREADS` and needs `SessionOptions` in code; that is a separate change.
- The benchmarking, grader and integration services.
- Docker CPU quotas, which torch ignores when choosing its thread count.

## Decisions

- **The environment variable, not `torch.set_num_threads` in code.** The variable is read before torch spins up its pool, covers every torch entry point in the process (`VectorStoreManager`, `vectorstore_connector`, the retrievers), and is the exact mechanism the A/B measured. A code call would need a hook in each service entrypoint, and an early import could still race it.
- **One key, `data_manager.torch_threads`, applied to both containers.** Both run the same embedding model from the same `data_manager.embedding_class_map`, the same way `device` is already shared. Separate keys would be configuration nobody has asked for.
- **Default 8.** It was the fastest of 32, 8, 4 and 1 on the host (16 ms, vs 19 ms at 4 and 42 ms at 1) and keeps two busy containers at half the cores.
- **Validate in a helper module, called from `_render_compose_file`.** Per the project rule, new logic lives in a small tested module, not in a large manager.
- **Conditional render.** The template emits the variable only when `torch_threads` is supplied, so callers that render the template without it (tests, podman paths) produce no empty `OMP_NUM_THREADS: ""`.

## Risks / Trade-offs

- [8 is too few on a big, idle machine] → `data_manager.torch_threads` raises it; the cost at 8 is small (one passage in 16 ms).
- [A rendered file that predates this change stays uncapped until the next `archi create`] → the dev redeploy re-renders it; other teams get it on their next create.
- [The cap also limits other OpenMP code in those containers] → nothing else there is compute-bound; the 32 ingest chunking workers are processes, not OpenMP threads.

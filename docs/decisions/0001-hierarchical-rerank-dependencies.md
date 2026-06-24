# 0001 — Dependencies for hierarchical-rerank-retrieval

**Status:** Accepted (branch-scoped: `feat/hierarchical-rerank-impl`)
**Task:** `1.1 Add llama-index-core and flashrank to project dependencies; record chatbot/data-manager image-size delta.`
**Change:** `openspec/changes/add-hierarchical-rerank-retrieval`

## Context

The hierarchical-rerank-retrieval change needs (a) a structure-aware node
parser for parent-child chunking at ingestion and (b) a CPU cross-encoder
reranker for retrieval. Both must run on the existing CPU-only service images
without pulling in a GPU/torch-heavy stack or bloating the chatbot/data-manager
images.

## Decision

Add two pinned dependencies to `requirements/requirements-base.txt` (the
single source of truth that `scripts/dev/build_docker_images.sh` concatenates
into the `base-python-image` / `base-pytorch-image` requirements consumed by
the chat and data-manager images):

| Package | Pin | Why |
| --- | --- | --- |
| `llama-index-core` | `==0.14.19` | The lightweight **core** package only — NOT the full `llama-index` meta-package (which drags in dozens of integration sub-packages). Provides `SentenceSplitter` / `MarkdownElementNodeParser` node parsing. `0.14.19` is the **last** release that requires `nltk>3.8.1`; `0.14.20+` bumped to `nltk>=3.9.3`, which conflicts with the existing `nltk==3.9.1` pin. Pinning to `0.14.19` keeps the change additive with no forced bump of `nltk` (and therefore no risk to `unstructured` / `sentence-transformers`). |
| `flashrank` | `==0.2.10` | CPU/ONNX cross-encoder reranker. ~MBs, no torch/GPU. Its heavy transitive deps (`onnxruntime`, `tokenizers`, `protobuf`, `huggingface-hub`) are **already present** in the base image, so it adds almost nothing on disk. |

### Rejected alternatives

- **Full `llama-index` meta-package** — pulls dozens of unused integration
  packages; rejected for image bloat (design D-section "Dependency weight").
- **`bge-reranker-large` (torch) reranker** — needs torch/GPU and bloats the
  image; the dev-box GPUs are claimed by the model server. Rejected per design
  decision D4 in favour of FlashRank (ONNX, CPU).
- **Bumping `nltk` to satisfy `llama-index-core` 0.14.20+** — rejected to keep
  this change strictly additive; pinning to `0.14.19` avoids touching an
  existing transitive-dependency pin.

## Image-size delta (measured)

Built images could not be produced in this environment (no container runtime),
so the delta is recorded as the **incremental installed on-disk footprint** of
the new packages into a clean `site-packages`, which is the dominant contributor
to the layer added on top of the shared `a2rchi-python-base` image. Both the
chat and data-manager images derive `FROM` that base, so the delta applies to
both equally.

| Component | Incremental size |
| --- | --- |
| `llama-index-core` (+ small deps: `banks`, `griffe`, `aiosqlite`, `dirtyjson`, `tinytag`, `nest-asyncio`, `deprecated`, `llama-index-workflows`, `llama-index-instrumentation`) | ~33 MB total |
| └ of which `llama_index` package itself | ~29 MB |
| `flashrank` (package only) | ~44 KB |
| `flashrank` heavy deps (`onnxruntime`, `tokenizers`, `protobuf`, `huggingface-hub`) | 0 (already in base image) |
| **Total per-image delta (chat & data-manager)** | **~33 MB** |

Notes:
- FlashRank downloads its ONNX reranker model (a few MB, e.g.
  `ms-marco-MiniLM-*`) to a runtime cache on first use — it is **not** baked
  into the image, so it does not count toward the build-time delta.
- No existing pin changed; `nltk` stays at `3.9.1`. Resolution verified with a
  full `pip install --dry-run -r requirements-base.txt` (no conflicts).

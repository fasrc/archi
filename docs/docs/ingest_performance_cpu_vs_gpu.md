# Ingest Performance: CPU vs GPU Embedding

Measured on fasrc-dev (`holygpu7c0717.rc.fas.harvard.edu`) on 2026-08-06.
Tracking issue: fasrc/archi#215.

## Hardware

| | |
| --- | --- |
| Host | holygpu7c0717 (archi.rc.fas.harvard.edu) |
| GPUs | 4× Tesla V100-PCIE-32GB |
| GPU 0 | idle → embedding (824 MiB / 32,768 MiB = 2.5%) |
| GPU 1 | idle |
| GPU 2–3 | vLLM Qwen 3.6 (30,260 MiB each) |
| Embedding model | all-MiniLM-L6-v2 (22M params, 384-dim, ~80 MB) |

## CPU Baseline

| Setting | Value |
| --- | --- |
| torch | 2.6.0+cpu (CPU-only build) |
| Visible cores | 1 (`nproc` inside the container) |
| `parallel_workers` | 32 (contending on 1 core) |
| `scrape_workers` | 8 |
| Files embedded | 817 |
| Vectorstore chunks | 6,023 |
| NLTK errors | 10 (WordListCorpusReader race, #119) |

### Phase Timing (CPU)

| Phase | Start (UTC) | End (UTC) | Duration |
| --- | --- | --- | --- |
| Scrape (8 workers) | 15:58:37 | 16:07:10 | 8m 33s |
| Git clone + LLM categorize | 16:07:10 | 16:16:03 | 8m 53s |
| Chunk + parse (32 threads) | 16:16:03 | 16:16:34 | 0m 31s |
| **Embed (CPU)** | **16:16:34** | **17:00:37** | **44m 03s** |
| **TOTAL** | **15:58:33** | **17:00:37** | **62m 04s** |

Per-file embed: **3.23s** (2,643s / 817 files).
Embed phase is **71%** of total wall clock.

## GPU Result

| Setting | Value |
| --- | --- |
| torch | 2.6.0+cu124 (CUDA 12.4) |
| Device | cuda:0 (Tesla V100-PCIE-32GB, compute cap 7.0) |
| `parallel_workers` | 32 |
| `scrape_workers` | 8 |
| Files embedded | 1,116 (+299 from code_suffixes fix) |
| Vectorstore chunks | 6,854 (+831) |
| NLTK errors | 6 |
| Disallowed suffix warnings | 772 (legitimate: .png, .rst, .out, .dat, etc.) |

### Phase Timing (GPU)

| Phase | Start (UTC) | End (UTC) | Duration |
| --- | --- | --- | --- |
| Scrape (8 workers) | 17:58:31 | 18:06:13 | 7m 42s |
| Git clone + LLM categorize | 18:06:13 | 18:19:04 | 12m 51s |
| **Chunk + parse + embed (GPU)** | **18:19:04** | **18:20:23** | **1m 19s** |
| **TOTAL** | **17:58:27** | **18:20:23** | **21m 56s** |

Per-file embed: **0.07s** (79s / 1,116 files).
Embed phase is **6%** of total wall clock (was 71%).

## Comparison

| Metric | CPU (817 files) | GPU (1,116 files) | Speedup |
| --- | --- | --- | --- |
| Embed phase | 44m 03s (2,643s) | 1m 19s (79s) | **33.5×** |
| Per-file embed | 3.23s | 0.07s | **46×** |
| Total ingest | 62m 04s (3,724s) | 21m 56s (1,316s) | **2.8×** |

The GPU run embedded **37% more files** (1,116 vs 817) in **1/33 the time**.

## Two Blockers Found and Resolved

### 1. TEI incompatible with V100

HuggingFace Text Embeddings Inference (TEI) default image is compiled for Ampere
(sm_80). V100 is Volta (sm_70):

```
ERROR: Could not start Candle backend: Runtime compute cap 70 is not compatible
with compile time compute cap 80
```

No official Volta tag exists. The solution was to skip TEI entirely and give the
data-manager container direct GPU access.

### 2. torch CPU-only in the data-manager image

The data-manager container shipped `torch 2.6.0+cpu`. Even with Docker GPU
passthrough (`deploy.resources.reservations.devices`) and `device: cuda:0`, torch
raised `Torch not compiled with CUDA enabled`.

**Solution:** created `Dockerfile-data-manager-gpu`, following the existing
pattern where `Dockerfile-chat-gpu` differs from `Dockerfile-chat` only in the
base image (`FROM a2rchi-pytorch-base` instead of `FROM a2rchi-python-base`).
Extended the compose template (`base-compose.yaml`) to select the GPU Dockerfile
and add the NVIDIA deploy block when `--gpu-ids` is set.

## Bonus Find: code_suffixes Template Bug

The deploy config set 23 HPC-specific code suffixes for git ingest:

```yaml
code_suffixes: [.py, .sh, .c, .cpp, .h, .hpp, .f90, .f, .f95, .cu, .R, .Rmd,
                .m, .jl, .sbatch, .slurm, .def, .json, .yaml, .yml, .md, .ipynb, .toml]
```

But `base-config.yaml` never rendered `code_suffixes` into the git source block —
it only rendered `enabled`, `visible`, and `schedule`. The git scraper fell back
to its hardcoded web-app-centric default (`.py`, `.js`, `.ts`, `.java`, `.go`,
`.rs`, `.c`, `.cpp`, etc.), which lacks every HPC-specific suffix.

**Silent since PR #108** (loader/collector parity). Every ingest dropped 1,013
files with no visible error — just warnings:

| Suffix | Files dropped per ingest |
| --- | --- |
| .sbatch | 139 |
| .f90 | 53 |
| .R | 21 |
| .m | 19 |
| .cu | 18 |
| .slurm | 18 |
| .ipynb | 12 |
| .def | 9 |
| .Rmd | 2 |
| .jl | 1 |

**Fix:** 2-line conditional in `base-config.yaml` that renders `code_suffixes`
when defined in the deploy config.

## Corpus Impact

| | Before (suffix bug) | After (suffix fix) | Delta |
| --- | --- | --- | --- |
| Files embedded | 817 | 1,116 | +299 (+37%) |
| Vectorstore chunks | 6,023 | 6,854 | +831 (+14%) |

The +299 files are Fortran (.f90), CUDA (.cu), R (.R/.Rmd), SLURM job scripts
(.sbatch/.slurm), Singularity definitions (.def), Jupyter notebooks (.ipynb),
Julia (.jl), and MATLAB (.m) — HPC examples now retrievable for the first time.

## Configuration

### Enable GPU embedding (deploy config)

```yaml
# deploy/fasrc-dev/config.yaml
data_manager:
  embedding_class_map:
    HuggingFaceEmbeddings:
      kwargs:
        model_kwargs:
          device: cuda:0
```

```bash
# deploy/scripts/lib.sh
GPU_IDS="0"
```

### Revert to CPU

Remove the `embedding_class_map` override from the deploy config (the template
defaults to `device: cpu`) and set `GPU_IDS=""` in `lib.sh`. Redeploy — it will
use `Dockerfile-data-manager` (CPU) instead of `Dockerfile-data-manager-gpu`.

### GPU memory

GPU 0 went from 0 MiB to 824 MiB (2.5% of 32 GB). The model is 80 MB; the rest
is CUDA runtime overhead.

## What Changed (code)

| File | Change |
| --- | --- |
| `Dockerfile-data-manager-gpu` | New. `FROM a2rchi-pytorch-base` (torch+CUDA). |
| `base-compose.yaml` | GPU Dockerfile selection + NVIDIA env/deploy block for data-manager when `gpu_ids` set. |
| `base-config.yaml` | Render `code_suffixes` when defined in the deploy config. |
| `deploy/scripts/lib.sh` | `GPU_IDS` variable, passed as `--gpu-ids` to `archi create`. |
| `deploy/fasrc-dev/config.yaml` | `device: cuda:0` override in `embedding_class_map`. |

## Next Steps

1. **New bottleneck: Git clone + LLM categorize** (12m 51s, was 8m 53s — more
   files). Scales linearly with file count; batching the categorization LLM calls
   would help.
2. **Incremental embedding:** skip files whose content hash already has vectors in
   Postgres. A no-change re-ingest would be near-instant instead of 22 minutes.
3. **772 remaining "disallowed suffix" warnings** are legitimate (.png, .rst,
   .out, .dat, .tif, .pdf, etc.) — not actionable but could be quieted to DEBUG.
4. **NLTK WordListCorpusReader race** (#119): 6 files failed (was 10 on CPU).
   Known lazy-load collision under parallel tokenization.

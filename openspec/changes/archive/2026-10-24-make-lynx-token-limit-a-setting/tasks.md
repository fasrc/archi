Rules for every task:
- Read `design.md` (D1-D6) first. Issue #773 is the work order.
- TDD: write the failing test first, run it and watch it FAIL, then write the minimum code.
- The gate `bash scripts/gate.sh` runs before each commit. Never use `--no-verify`.
- No test needs a GPU, `vllm`, `transformers`, Slurm, Apptainer or the network.

## 1. Backend (D3)

- [x] 1.1 `tests/unit/test_lynx_backends.py`: `VllmBackend(model, revision,
      context_tokens=32000)` calls the fake `LLM` with `max_model_len=32000`, the default
      is `max_model_len=8000`, and `make_backend("vllm", ..., context_tokens=32000)` passes
      it on. Then `backends.py`.

## 2. Scorer CLI (D1, D2)

- [x] 2.1 `tests/unit/test_lynx_score.py`: with `context_tokens=16000`, a 15,400-token
      row is scored and a 15,401-token row is `skipped_overflow` and never reaches the stub;
      the Lynx file records `token_budget.context_tokens` (8,000 by default, 16,000 when
      given). Then `score_bundle`.
- [x] 2.2 `tests/unit/test_lynx_score.py`: `--context-tokens 32000` reaches the backend
      factory as `context_tokens=32000` and the default is 8,000; `600`, `0` and `-1` exit 1
      with no backend built and no Lynx file. Then `main`.

## 3. Report (D4)

- [x] 3.1 `tests/unit/test_lynx_compare_block.py`: each arm's entry carries
      `context_tokens` from its file's `token_budget` (8,000 and 32,000 side by side), a
      refused file carries `None`, the rendered table has a "token limit" column, and the
      section prose names no fixed limit. Then `report.py`.

## 4. Slurm job (D5)

- [x] 4.1 `tests/unit/test_lynx_sbatch.py`: the dry run ends with `--context-tokens 8000`;
      `CONTEXT_TOKENS` is `"8000"`; after the v1.1 swap the dry run names the v1.1 model id,
      revision `00de9f9062d93a95e4e359d3c92b8ea1d0114738`, `--tensor-parallel-size 1` and
      `--context-tokens 32000`; a `CONTEXT_TOKENS` of `600`, `0`, `""`, `abc` or `8k` is
      refused before the container starts and the message names `CONTEXT_TOKENS`. Then
      `lynx_score.sbatch`.

## 5. Docs (D5, D6)

- [x] 5.1 `tests/unit/test_benchmarking_docs_lynx.py`: the Lynx section names
      `--context-tokens`, `CONTEXT_TOKENS`, the v1.1 model id and revision, `32000`, and
      `VLLM_ALLOW_LONG_MAX_MODEL_LEN`, and no longer says the limit is a fixed 8,000 tokens.
      Then the "Lynx faithfulness (offline)" section of `docs/docs/benchmarking.md`.

## 6. Prose and gate

- [x] 6.1 `score.py` module docstring and the `backends.py` comment describe the setting,
      not a fixed 8,000.
- [x] 6.2 `python -m pytest tests/unit -k lynx -q` passes, `mkdocs build --strict -f
      docs/mkdocs.yml` passes, `openspec validate make-lynx-token-limit-a-setting
      --strict` passes, and `bash scripts/gate.sh` exits 0.

## 7. Real-model check (dev host)

- [x] 7.1 Score a rebuilt golden-set bundle with Lynx 8B v1.1 at `--context-tokens
      32000` on GPU 0 (`VLLM_FLASH_V100_DISABLE_PAGED_PREFILL=1`, memory share 0.85 through
      a host-local wrapper around `score.main`), and confirm that rows above 16,000 tokens
      are scored and `token_budget.context_tokens` is 32000.

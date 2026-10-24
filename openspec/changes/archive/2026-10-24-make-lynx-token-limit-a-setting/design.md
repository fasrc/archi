## Context

Issue fasrc/archi#773 is the work order. Anchors are against `origin/dev` at `81eeaeec`,
where the scorer (#734), the Slurm job (#735), the GPU split (#755) and the Lynx-form
parser (#771) are merged.

- `backends.py:12-14`: `CONTEXT_TOKENS = 8000`, `MAX_NEW_TOKENS = 600`.
- `score.py:201`: a row is `skipped_overflow` when `tokens + MAX_NEW_TOKENS >
  CONTEXT_TOKENS`; `score.py:243-246` writes both numbers under `token_budget`.
- `backends.py:63-68`: `LLM(model, revision, tokenizer_revision, tensor_parallel_size)`, no
  `max_model_len`, so vLLM takes the model's `max_position_embeddings` (8,192 for v1.0 8B
  and 70B, 131,072 for v1.1).
- Prose that names 8,000: `score.py:15`, `report.py:246`, `docs/docs/benchmarking.md:481`,
  `openspec/specs/lynx-faithfulness-scoring/spec.md:37`.

Lynx 8B v1.1 (`PatronusAI/Llama-3-Patronus-Lynx-8B-Instruct-v1.1` at
`00de9f9062d93a95e4e359d3c92b8ea1d0114738`): fp16, about 16 GB, `rope_scaling`
`llama3` with factor 8 over an original 8,192, model card "The maximum sequence length is
128000 tokens". The two model cards at the pinned revisions differ only in the model name,
that sentence and the benchmark table, so the prompt template that `prompt.py` copies is
the same byte for byte. The v1.1 chat template differs from v1.0's only in adding the
assistant header without checking `add_generation_prompt`; the scorer passes
`add_generation_prompt=True`, so both give the same text.

## Goals / Non-Goals

**Goals:** the operator chooses the token limit; the vLLM engine reserves exactly that
limit; every Lynx file and report says which limit scored it; the job can run Lynx 8B v1.1
at 32,000 tokens by swapping commented lines; the default run is unchanged.

**Non-Goals:** making v1.1 the job's default; a scorer setting for vLLM's GPU memory share,
batch size or prefill chunking; truncating long rows; a model table inside the scorer;
re-scoring old runs.

## Decisions

### D1. The limit is `--context-tokens N`, default 8,000

`main` gets `--context-tokens` (`type=int`, default `CONTEXT_TOKENS`, `metavar="N"`).
`CONTEXT_TOKENS` stays in `backends.py` as the default, so the v1.0 job and every existing
fixture keep 8,000. `score_bundle(..., context_tokens=CONTEXT_TOKENS)` uses it in the skip
rule and writes it as `token_budget.context_tokens`. `MAX_NEW_TOKENS` stays 600: it is the
model card's generation length and is the same for v1.1.

Alternative: derive the limit from the model's `config.json`. Rejected: the scorer would
need the model files or `transformers` before it reads the bundle, the stub backend has no
model, and the limit is a cost choice as well as a model property (32,000 instead of
v1.1's 128,000 saves about 12 GiB of KV cache).

### D2. A limit of 600 or less is a refusal, before the bundle is read

No row can fit when the limit is not above the 600 new tokens. The check sits beside the
`--tensor-parallel-size` check at the top of the `try` block, so the scorer exits 1, reads
no bundle, builds no backend and writes no file. A non-integer is a usage error of
`argparse` (exit 2). There is no upper bound in the scorer: vLLM refuses a `max_model_len`
above the model's trained length unless `VLLM_ALLOW_LONG_MAX_MODEL_LEN=1` is set (and
warns that positions beyond it "lead to nan"). That refusal is the guard for v1.0 or 70B
with too large a limit; the docs say never to set the variable.

### D3. The vLLM backend passes `max_model_len=context_tokens` every time

`VllmBackend(model, revision, *, tensor_parallel_size=1, context_tokens=CONTEXT_TOKENS)`
and `make_backend(..., context_tokens=CONTEXT_TOKENS)`, keyword-only like the GPU count;
the stub ignores it. Two reasons. First, memory: without it vLLM sizes the KV cache for
the model's whole window, about 128 KiB per token for Llama-3.1-8B (32 layers x 8 KV heads
x 128 dims x K and V x 2 bytes), so 16 GiB for v1.1's 131,072 tokens beside 16 GB of
weights; at 32,000 tokens it is about 4 GiB. Second, one number: the skip rule guarantees
prompt plus 600 is at most the limit, and the engine enforces the same limit, so vLLM
never rejects a row the scorer sent. At the default 8,000, v1.0 loads as before with a KV
budget of 8,000 instead of 8,192; no row that the scorer sends is longer.

Alternative: pass `max_model_len` only when it is not the default. Rejected for the same
reason as D1 of the GPU split: one code path, and a test sees the value in the default case.

### D4. The report shows each arm's limit

`_block_entry` adds `context_tokens` from the document's `token_budget` (`None` for a
refused file), and the Lynx table gets a "token limit" column. The prose says rows are
skipped when they do not fit in the arm's token limit, with no number. Every Lynx file the
scorer has written already holds `token_budget.context_tokens`, so old files show 8,000.
`_mismatch` does not check `token_budget`: the limit does not decide whether a file
belongs to an arm.

The paired test is unchanged. It pairs only questions that both arms scored, so arms with
different limits still give a valid paired comparison over the shared questions, and the
excluded list names each `skipped_overflow`. The new column makes a difference in limits
visible next to it.

### D5. The job sets the limit once, as `CONTEXT_TOKENS`, with a v1.1 variant

The settings block gets `CONTEXT_TOKENS="8000"`, and the command passes `--context-tokens
"$CONTEXT_TOKENS"`. The job refuses a `CONTEXT_TOKENS` that is not a whole number above
600 before it starts a container, also in the dry run, like `GPUS`. A commented Lynx 8B
v1.1 variant sets its `MODEL_ID`, `MODEL_REVISION` and `CONTEXT_TOKENS="32000"`; it runs on
one GPU, so it needs no `##SBATCH` lines. The 70B variant keeps 8,000 (its window is
8,192), so it needs no `CONTEXT_TOKENS` line.

32,000 because the longest of the 425 rebuilt golden-set prompts is 25,610 tokens, so
every row fits with room to spare, and the KV cache stays about 4 GiB. At 16,000, 396 of
425 fit.

### D6. The prompt requirement does not change

v1.1's model-card template is byte-identical to v1.0's, and its chat text for the
scorer's call is identical too, so `prompt.py` and its requirement stand. The job and the
docs name the v1.1 revision, so a run says which model scored it.

## Risks / Trade-offs

- [The dev host's 32 GB V100s cannot start v1.1 at 32,000 tokens at vLLM's default GPU
  memory share of 0.9: on 2026-10-24 the engine reserved 10.74 GiB of KV cache, CUDA graph
  capture then took 2.67 GiB, and the sampler warm-up with 256 placeholder requests ran out
  of memory before any prompt.] → At a share of 0.85 the same engine starts (9.24 GiB of KV
  cache, 75,696 tokens) and scored a 25,610-token row in 27.9 s with reasoning that cites
  the row's context. The cluster GPUs (A100 and H200) have room for 16 GB of weights and
  4 GiB of KV cache at the default share. The dev-host real-model check runs the scorer's
  own `main` through a host-local wrapper that lowers the share; the scorer gets no memory
  setting until a cluster run needs one.
- [A limit above the model's trained length ends the scorer with a vLLM error, not a
  "refused" exit 1.] → The docs say so; the error happens before any row is scored, and no
  Lynx file is written.
- [v1.1's verdicts on long inputs are not measured by its model card beyond HaluBench.] →
  The Lynx-vs-RAGAS comparison on the five rebuilt runs reports v1.1's agreement with RAGAS
  over every rebuilt row.
- [Arms scored with different limits are compared in the paired test.] → The table shows
  both limits, and the excluded list names every `skipped_overflow`.

## Migration Plan

None. The default limit is unchanged, and old Lynx files already record their limit.

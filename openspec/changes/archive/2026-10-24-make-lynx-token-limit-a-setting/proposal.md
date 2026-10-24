## Why

The Lynx scorer skips almost every golden-set question. The limit is hard-coded:
`scripts/benchmarking/lynx/backends.py:13` sets `CONTEXT_TOKENS = 8000` (from the v1.0
model card, "The maximum sequence length is 8000 tokens"), and `score.py:201` marks a row
`skipped_overflow` when its prompt tokens plus 600 is more than that. On 2026-10-24, five
past golden-set runs on one corpus were rebuilt into judge bundles from their kept
Postgres volume, and every prompt was counted with the Lynx tokenizer through the chat
template, as the scorer counts. Only 12 to 16 rows per run fit (69 of 425 in all). The
median prompt is about 9,000 tokens and the longest 25,610. A Lynx PASS rate on golden-set
data today covers only short-context questions. Lynx 70B has the same 8,192-token window,
so it does not help (fasrc/archi#773).

`PatronusAI/Llama-3-Patronus-Lynx-8B-Instruct-v1.1` at
`00de9f9062d93a95e4e359d3c92b8ea1d0114738` reads up to 128,000 tokens. It is Lynx 8B
fine-tuned again from Llama-3.1-8B-Instruct, which was trained on long inputs. Its model
card has the same prompt template byte for byte and reports HaluBench 84.3% against 82.9%
for v1.0. At a 32,000-token limit every rebuilt row fits (425 of 425); at 16,000, 396 of
425 fit.

## What Changes

- `score.py` takes `--context-tokens N`, with 8,000 as the default. A row is
  `skipped_overflow` when its prompt tokens plus 600 is more than `N`. The scorer refuses
  an `N` of 600 or less (no row could fit) with exit 1, before it reads the bundle and
  before it builds the backend. The Lynx file's `token_budget.context_tokens` records `N`.
- The vLLM backend builds `LLM` with `max_model_len=N`. So vLLM sizes its KV cache for the
  limit, not for the model's whole trained length (131,072 tokens for v1.1, which does not
  fit beside the weights on a 32 GB GPU), and the scorer's skip rule and the engine's own
  limit are the same number. vLLM refuses a `max_model_len` above the model's trained
  length, which guards against a v1.0 or 70B run with too large a limit.
- The `compare_runs.py` Lynx block shows each arm's recorded limit, and its prose no longer
  says 8,000.
- `scripts/benchmarking/slurm/lynx_score.sbatch` has a `CONTEXT_TOKENS` setting (`"8000"`)
  that it passes as `--context-tokens`, refuses a `CONTEXT_TOKENS` that is not a whole
  number above 600, and holds a commented Lynx 8B v1.1 variant (its model id, its revision
  and `CONTEXT_TOKENS="32000"`, on one GPU). The active settings stay Lynx 8B v1.0.
- The "Lynx faithfulness (offline)" docs section gives `--context-tokens`, the v1.1
  variant, why 32,000, and that vLLM refuses a limit above the model's trained length.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `lynx-faithfulness-scoring`: the overflow limit is the `--context-tokens` setting; the
  vLLM backend reserves that limit; the Lynx block shows each arm's limit; the Slurm job
  has a `CONTEXT_TOKENS` setting and a v1.1 variant; the docs describe both.

## Impact

- Code: `scripts/benchmarking/lynx/score.py`, `backends.py`, `report.py`;
  `scripts/benchmarking/slurm/lynx_score.sbatch`.
- Docs: `docs/docs/benchmarking.md`.
- Tests: `tests/unit/test_lynx_score.py`, `test_lynx_backends.py`,
  `test_lynx_compare_block.py`, `test_lynx_sbatch.py`, `test_benchmarking_docs_lynx.py`.
  No test needs a GPU, `vllm`, Slurm, Apptainer or the network.
- No new dependency; `scripts/benchmarking/lynx/` still imports only the standard library.
- Unchanged: the default limit (8,000) and so every existing Lynx file and result; the
  prompt (`prompt.py`); the parser; the 600 new tokens.
- Out of scope: making v1.1 the job's default model, and a GPU-memory setting for the
  scorer.

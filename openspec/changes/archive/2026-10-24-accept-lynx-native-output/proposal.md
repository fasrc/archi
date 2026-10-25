## Why

The Lynx scorer counts no verdict from real Lynx output. On 2026-10-24,
`PatronusAI/Llama-3-Patronus-Lynx-8B-Instruct` at revision
`5523f869eb5eecb4bca417b6e6d3532e746a9664` (the revision
`scripts/benchmarking/slurm/lynx_score.sbatch` pins) judged a five-row bundle through
`python -m scripts.benchmarking.lynx.score --backend vllm`: one V100, vLLM 1.1.0, the
checkpoint's own fp16, greedy decoding. It gave the right verdict for all 4 scorable rows
(2 faithful answers PASS, 2 contradicting answers FAIL), and the scorer recorded all 4 as
`parse_error`: `lynx_scored 0`, `lynx_faithfulness null`. Each output had this form:

```text
{"REASONING": ['The CONTEXT explicitly states that netscratch is not backed up.', 'The ANSWER claims that netscratch is backed up nightly to tape, which directly contradicts the information provided in the CONTEXT.', 'The CONTEXT also mentions that files older than 90 days are deleted, which implies that there is no backup process in place.'], "SCORE": FAIL}
```

That is not JSON, for two reasons: the `REASONING` items are single-quoted, and the
`SCORE` value is a bare word. `scripts/benchmarking/lynx/parse.py` (#734, design D4 of
`openspec/changes/archive/2026-10-23-add-lynx-faithfulness-scorer/`) reads only JSON, so
it stops at the first `'` (char 15) and every row of every real run is a parse error. The
unit tests did not see this because every fixture, and the stub backend's output, is
valid JSON with `"SCORE": "PASS"`.

This is the format Lynx was trained on, not an accident of one run. Patronus's own
inference code (`patronus-ai/Lynx-hallucination-detection` at
`241fc162cf5e76a01138128a97b34bdb2b343bee`,
`mcli/git_repo_example/mcli_inference_v1.py` lines 94-108) splits the reasoning on
`"', '"`, which assumes single-quoted items, and falls back from `"SCORE":\s*"(\w+)"` to
`"SCORE":\s*(\w+)`, the bare word. The model card at the pinned revision (README lines
59-64) gives only the template `{"REASONING": <your reasoning as bullet points>, "SCORE":
<your final score>}` and never promises JSON.

## What Changes

- `parse.py` reads each top-level object as JSON plus two forms that Lynx writes: a
  single-quoted string where a value goes, and a bare word as the value of `SCORE`. Valid
  JSON reads exactly as before. The verdict rule is unchanged: only `PASS` and `FAIL` are
  verdicts, and every refusal of D4 stays, including the cut-off answer, a misspelled
  verdict (now also as a bare word: `pass`, `PAS`, `PASS.`), conflicting objects, a stray
  `{` before the answer, and `NaN`, `Infinity` and float overflow.
- An apostrophe inside a single-quoted item (`'The ANSWER's claim'`) stays in the item: a
  `'` ends the string only when the next non-space character is `,`, `]`, `}` or the end of
  the text.
- The stub backend answers in Lynx's own form, so a `--backend stub` run reads the format a
  real run reads.
- The "Lynx faithfulness (offline)" section of `docs/docs/benchmarking.md` says which
  output forms the scorer reads and what is a parse error.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `lynx-faithfulness-scoring`: the parser reads Lynx's own output form as well as JSON; the
  docs say which forms the parser reads.

## Impact

- Code: `scripts/benchmarking/lynx/parse.py`, `scripts/benchmarking/lynx/backends.py`
  (`STUB_OUTPUT`).
- Docs: `docs/docs/benchmarking.md`.
- Tests: `tests/unit/test_lynx_parse.py`, `tests/unit/test_lynx_score.py`,
  `tests/unit/test_benchmarking_docs_lynx.py`. No test needs a GPU, `vllm`,
  `transformers`, Slurm, Apptainer or the network.
- No new dependency; `scripts/benchmarking/lynx/` still imports only the standard library.
- Unchanged: `strict_loads`, which `report.py` uses to read the Lynx file (that file is
  JSON that `score.py` writes), and `prompt.py` (the model-card prompt).
- Out of scope: single-quoted keys; whether Lynx 70B writes the same form, and whether
  output is right when vLLM splits the model over GPUs (both need the cluster run, #737).

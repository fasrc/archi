Rules for every task:
- Read `design.md` (D1-D5) first. The proposal holds the real outputs and their source.
- TDD: write the failing test first, run it and watch it FAIL, then write the minimum code.
- The gate `bash scripts/gate.sh` runs before each commit. Never use `--no-verify`.
- No test needs a GPU, `vllm`, `transformers`, Slurm, Apptainer or the network.

## 1. Reader (D1-D4)

- [x] 1.1 `tests/unit/test_lynx_parse.py`: the four real Lynx 8B outputs of 2026-10-24,
      verbatim, parse to PASS, FAIL, PASS, FAIL with their reasoning lists; an apostrophe
      inside an item stays in the item; `\'` and the other escapes decode; the new
      refusals of D4 are parse errors; every existing test is unchanged. Then `parse.py`.
- [x] 1.2 `tests/unit/test_lynx_parse.py`: `_read_object` returns what
      `json.JSONDecoder().raw_decode` returns for valid JSON objects, and refuses the JSON
      that `json` refuses. Then make it pass.

## 2. Scorer and stub (D5)

- [x] 2.1 `tests/unit/test_lynx_score.py`: a backend that answers in Lynx's form gives
      `lynx_scored 4`, `lynx_pass 2` and `lynx_faithfulness 0.5`; the stub's answer
      parses to PASS with reasoning `["stub backend"]`. Then `backends.STUB_OUTPUT`.

## 3. Docs

- [x] 3.1 `tests/unit/test_benchmarking_docs_lynx.py`: the Lynx section shows Lynx's own
      form and says what is a parse error. Then `docs/docs/benchmarking.md`.

## 4. Gate and real-model check

- [x] 4.1 `python -m pytest tests/unit -k lynx -q` passes, and `bash scripts/gate.sh`
      exits 0.
- [x] 4.2 A real Lynx 8B run on one GPU over the five-row bundle scores 4 rows (2 PASS,
      2 FAIL) and skips 1, with `lynx_faithfulness` 0.5.

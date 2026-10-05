## 1. Shared pure helpers (`src/utils/benchmark_provenance.py`)

- [x] 1.1 `model: sonnet` — RED: tests for `canonical_source_url` (trailing slash, root path, whitespace, query/fragment kept, non-string input) in a new `tests/unit/test_category_map_digest.py`
- [x] 1.2 `model: sonnet` — GREEN: add `canonical_source_url`; point `Benchmarker._canonical_source` at it; `tests/unit/test_benchmark_source_url_match.py` stays green unchanged
- [x] 1.3 `model: sonnet` — RED: tests for `category_map_records` / `category_map_digest` — order invariance, category change moves the digest, missing category → empty field, no-URL row skipped, tab/newline/`%` escaped, duplicate URL kept, `sha256:` prefix
- [x] 1.4 `model: sonnet` — GREEN: implement both helpers

## 2. Harness readings (`src/bin/service_benchmark.py`)

- [x] 2.1 `model: opus` — run the black-seam-scout agent on `service_benchmark.py`; record the verdict in this task line before editing. Verdict: black- and isort-clean; edit sites covered except `run()` and `load_new_configuration`; expected patch coverage about 90 %; hash the prompt as `load_agent_spec` reads it (`read_text`)
- [x] 2.2 `model: opus` — RED: tests for `ResultHandler.get_category_map` — success returns records + digest; factory missing or query raising returns `(None, "<unavailable: …>")` and never raises
- [x] 2.3 `model: opus` — GREEN: implement `get_category_map` beside `get_corpus_fingerprint`
- [x] 2.4 `model: opus` — RED: `handle_results` tests — equal digests → `true`; different → `false` + warning; either failed → `null`; both failed with the same text → `null`; corpus-fingerprint keys unchanged
- [x] 2.5 `model: opus` — GREEN: take the start reading beside `corpus_before` in `run()`, pass it as `category_map_before`, take the end reading in `handle_results`, write the three keys, hold the end records outside the JSON

- [x] 2.6 `model: opus` — RED/GREEN: each arm entry records `agent_md_sha256` of the prompt file the harness read for that arm (`null` when none); first confirm where the harness reads `agent_md_file` per arm and hash those same bytes

## 3. Persisted snapshot (`dump_artifacts`)

- [x] 3.1 `model: opus` — RED: `dump_artifacts` test with three arms — three `_category_map_<N>.tsv` files, `sha256(file)` equals each arm's end digest, `category_map_file` set; failed end reading → no file, `null`
- [x] 3.2 `model: opus` — GREEN: write the files and set `category_map_file` before `dump`

## 4. Verify

- [ ] 4.1 `model: sonnet` — `bash scripts/gate.sh` green (patch coverage ≥ 80 %)
- [x] 4.2 `model: sonnet` — `openspec validate record-category-map-digest --strict`

## 5. End-to-end check (after merge, per `AGENTS.md:58-63`)

- [ ] 5.1 `model: sonnet` — on the claw workstation, `archi evaluate --config-dir` a two-arm sweep over the 5-row anchor bank against an explicit stack (`benchmarking-<stack>`, `postgres-<stack>`, `data-manager-<stack>`); confirm the image runs the new code (the harness log line and `importlib` path of `src.utils.benchmark_provenance`), then check the artifact has the three new keys per arm, two `_category_map_<N>.tsv` siblings exist, `sha256(file)` equals each arm's end digest, and each arm's `agent_md_sha256` equals the sha256 of the prompt file that arm ran; record the commands and output on the PR

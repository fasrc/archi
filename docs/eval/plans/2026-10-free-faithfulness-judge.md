# Free Faithfulness Judge Experiment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the test material, lock it, score every judge, and produce the decision report that the pre-registration defines.

**Architecture:** A small Python package `ffj/` in a check folder of `fasrc/archi-bench-out`. It imports archi's bundle, RAGAS and Lynx code from a pinned archi worktree and writes material and scores to scratch. Pure logic (selection, edits, trimming, flags, decision) is unit-tested. Model runs are runbook steps that call that logic. An R script recomputes every reported number.

**Tech Stack:** Python 3.13 (conda env `archi`: ragas 0.3.5, langchain-openai 1.1.10, argilla 2.8.0), pytest, the Volta vLLM Singularity image for Lynx, OpenRouter and HUIT Bedrock for LLM judges, Argilla 2.8 on `:3080`, R (`/scratch/a2rchi/conda-envs/r-bench/bin/Rscript`).

**Spec:** `docs/eval/preregs/2026-10-free-faithfulness-judge.md` (design lock `e2c1f113`). Read it first; this plan argues from it and does not repeat its rationale.

## Global Constraints

- Design lock: `e2c1f1135e7bd76254b9e34e8e8c7feefd0526f5`. No judge scores any new item before the material lock commit (Task 13).
- Seed `20261027` for every random choice.
- Runs: `012746`, `054355` = new code, Qwen3.6; `034614`, `081227` = old code, Qwen3.6; `130451` = new code, Qwen3.8. Prefix `benchmarking-ragas-retrieval-ab-20261012_`.
- Lynx v1.0 `PatronusAI/Llama-3-Patronus-Lynx-8B-Instruct` rev `5523f869eb5eecb4bca417b6e6d3532e746a9664`, limit 8,000. Lynx v1.1 `PatronusAI/Llama-3-Patronus-Lynx-8B-Instruct-v1.1` rev `00de9f9062d93a95e4e359d3c92b8ea1d0114738`, limit 32,000, GPU memory share 0.85. A row fits when `prompt_tokens + 600 <= limit`. Lynx's prompt text is never edited.
- Incumbent S: RAGAS `faithfulness`, `huit_bedrock` / `us.anthropic.claude-sonnet-4-5-20250929-v1:0`. October RAGAS settings, passed explicitly every time: `timeout 600`, `max_workers 6`, `batch_size false`, embeddings `HuggingFace`. (The rebuilt bundles' manifests carry defaults of 180 and 16, so their settings must not be used.)
- OpenRouter: base URL `https://openrouter.ai/api/v1`, key `JUDGE_OPENROUTER_API_KEY` from `~/.secrets/archi-secrets.env`. Models `openai/gpt-oss-120b`, `nvidia/nemotron-3-ultra-550b-a55b:free`, `meta-llama/llama-3.3-70b-instruct`. Each is pinned with `extra_body={"provider": {"order": [<host>], "allow_fallbacks": False}}`, uses `temperature=0` and `seed=20261027`. Hosts are chosen in Task 11 among full-precision hosts with ≥ 128k context. Never write a key to any output.
- GPUs 0 and 1 only. GPUs 2 and 3 serve production Qwen.
- B: 40 harmful (8 each of types 1–5), 30 benign (15 paraphrase, 15 general advice). Short targets 20 harmful and 15 benign, floors 10 and 8. C: 20 items, 20 distinct donors, from `012746`, 10 short and 10 long. D: 40 real rows (16 with RAGAS < 0.5, 12 with 0.5–0.8, 12 with ≥ 0.8 drawn from B's base rows), plus 6 hidden planted items (3 harmful, 3 benign), plus a 20-row top-up only if fewer than 8 are graded "Yes". Dev split: 10 real rows from `130451` that are not in D, plus 4 planted items (2 harmful, 2 benign) that are not in B.
- Flags, margins, cutoffs, bars, the early stop and retries are exactly as in the spec's "Definitions", "Decision rule" and "Lynx input variants".
- Code and small outputs live in `checks/2026-10-free-faithfulness-judge/` of `fasrc/archi-bench-out`. Large inputs and outputs live under `/scratch/a2rchi/bench-runs/lynx-vs-ragas/ffj/` (`DATA` below). No archi `src/` change.
- No new third-party dependencies. BM25 is written inline.
- Commits: short, lowercase, no `Co-Authored-By`. Ask the operator before any push.

Paths used below:
- `ARCHI_PIN=/scratch/a2rchi/worktrees/archi-ffj`, an archi worktree detached at `e2c1f113`.
- `BENCH=/scratch/a2rchi/worktrees/bench-out-ffj`, a bench_out worktree on branch `ffj-2026-10`.
- `CHECK=$BENCH/checks/2026-10-free-faithfulness-judge`.
- `DATA=/scratch/a2rchi/bench-runs/lynx-vs-ragas/ffj`.
- `PY=/home/a2rchi/miniforge3/envs/archi/bin/python`.
- `SIF=/scratch/a2rchi/sifs/vllm_volta.sif`.
- Tests run with `cd $CHECK && PYTHONPATH=$ARCHI_PIN:$CHECK $PY -m pytest tests -q`.

## Review Focus

1. **Answers with code fences, tables or numbered lists.** A unit splitter or specific extractor that cuts inside a fence, or treats "1." as a number specific, corrupts L0s/L1s units and base-row checks. Tests in Tasks 5 and 9.
2. **The same passage retrieved twice in one row.** Trimming must not spend two of its three slots on one text. The control's "no shared passage" test must compare text, not list position. Tests in Tasks 8 and 9.
3. **A reasoning model returns empty `content`, or JSON cut off at `finish_reason: "length"`.** This is a retry and then no verdict, never a PASS. Test in Task 11.
4. **The free tier's daily cap is hit mid-run.** A resumed run skips finished keys and never records a duplicate or a verdict for a capped call. Test in Task 11.
5. **A harmful edit is not actually unsupported.** Two cases: its new value already occurs in the documents, or its target value occurs twice in the answer and only one copy changes. Such an edit is rejected. Tests in Task 7.

---

### Task 0: Amend the pre-registration before the material lock (operator approval required)

**Files:**
- Modify: `docs/eval/preregs/2026-10-free-faithfulness-judge.md` (archi repo, PR to `dev`)

Two statements in the locked design are factually wrong. Both are found before any scoring, so they are fixed by a dated amendment section, not silently.

- [ ] **Step 1: Add a section `## Amendment 1 (<date>, before the material lock)` stating:**
  - The whole-document cut point is known, not unknown. The two search tools that add parent-less sources call `catalog.get_document(resource_hash, max_chars=4000)` (`src/archi/pipelines/agents/tools/local_files.py:324`, `:423`). That is a code constant, identical at both October commits `cd9658ba` and `7406721b`. The server returns `load_text_from_path(path)[:4000]` with no marker.
  - The recovery test therefore becomes: the source's text is `load_text_from_path(file)[:4000]`, and it is accepted only if (a) the file is `.md` or `.yml` (read raw, so deterministic), (b) its mtime is earlier than the run's start, and (c) its first 300 characters equal the stored `sources_trunc_content`. Any other source keeps its question out.
  - The old rule ("length within the cap") recovers 0 of 120 questions, because every one has a source over 4,000 characters.
  - The October result files do record `judge_usage`, aggregated over the 5 metrics and not per metric. The cost analysis uses that aggregate and S's measured faithfulness-only usage.
- [ ] **Step 2: Commit, push the branch and open a PR to `dev` after the operator says to.** Merging it is the operator's approval. Task 3 uses the amended rule only after that merge.

---

### Task 1: Workspace, constants and pinned imports

**Files:**
- Create: `$CHECK/ffj/__init__.py`, `$CHECK/ffj/config.py`, `$CHECK/tests/conftest.py`, `$CHECK/tests/test_config.py`, `$CHECK/README.md`

**Interfaces:**
- Produces: `ffj.config` constants `SEED`, `DESIGN_LOCK`, `RUNS: dict[str, tuple[str, str]]` (short id → (arm, model)), `RUN_PREFIX`, `L0`, `L1` (dicts with `model`, `revision`, `limit`), `NEW_TOKENS = 600`, `DOC_CAP = 4000`, `OCT_RAGAS: dict`, `S_JUDGE: dict`, `OPENROUTER_BASE`, `OR_MODELS: dict[str, str]` (`"G"`, `"N"`, `"M"` → model id), `PAIRED_MARGIN = 0.05`, `CUTOFFS = (1.0, 0.5)`, `DATA: Path`.

- [ ] **Step 1: Create the worktrees.**
  - `git -C /home/a2rchi/archi-openai-compat worktree add --detach $ARCHI_PIN e2c1f113`
  - `git -C /home/a2rchi/archi-openai-compat/bench_out worktree add -b ffj-2026-10 $BENCH main`
  - `mkdir -p $DATA`
- [ ] **Step 2: Write the failing tests.**

```python
def test_constants_match_prereg():
    assert config.SEED == 20261027
    assert config.DESIGN_LOCK.startswith("e2c1f113")
    assert config.OCT_RAGAS == {"timeout": 600, "max_workers": 6, "batch_size": False, "embedding_model": "HuggingFace"}
    assert config.L0["limit"] == 8000 and config.L1["limit"] == 32000 and config.NEW_TOKENS == 600

def test_archi_imports_come_from_pin():
    import src.utils.judge_bundle as jb
    assert jb.__file__.startswith("/scratch/a2rchi/worktrees/archi-ffj/")
```

- [ ] **Step 3: Run the tests and see them fail.**
- [ ] **Step 4: Implement `ffj/config.py`, plus a `conftest.py` that fails fast when `PYTHONPATH` does not start with `ARCHI_PIN`.** The README states the folder's purpose and points to the spec and this plan.
- [ ] **Step 5: Run the tests and see them pass. Then commit `ffj: scaffold check folder and pinned constants`.**

### Task 2: Bundle I/O with October settings

**Files:**
- Create: `$CHECK/ffj/bundles.py`, `$CHECK/tests/test_bundles.py`

**Interfaces:**
- Consumes: `src.utils.judge_bundle.write_judge_bundle`, `src.utils.judge_scorer.load_bundle`.
- Produces:
  - `write_bundle(root: Path, basename: str, rows: list[dict]) -> Path`. Each row dict has `key`, `user_input`, `retrieved_contexts`, `response` and `reference`. It writes `<root>/<basename>-arm0/` with `enabled_metrics=["faithfulness"]`, `ragas_settings` from `OCT_RAGAS`, `judge_model={"id": "ffj-material", "revision": DESIGN_LOCK}`, and the code version `{"archi": DESIGN_LOCK}`. It returns the folder.
  - `read_rows(bundle: Path) -> dict[str, dict]`, which maps each key to its row, key included.

- [ ] **Step 1: Write failing tests.**
  - `test_round_trip_keeps_rows_and_order`.
  - `test_manifest_carries_october_settings`, which asserts `manifest["ragas_run_config_kwargs"]["timeout"] == 600` and `["max_workers"] == 6`.
  - `test_refuses_existing_folder`, which raises.
  - `test_lynx_reader_accepts_bundle`, which calls `scripts.benchmarking.lynx.score.read_bundle` on the folder.
- [ ] **Step 2: Run them and see them fail. Step 3: Implement. Step 4: Run them and see them pass. Step 5: Commit `ffj: bundle io with october ragas settings`.**

### Task 3: Recovery of the missing questions (after Task 0 merges)

**Files:**
- Create: `$CHECK/ffj/recover.py`, `$CHECK/scripts/copy_docs.sh`, `$CHECK/tests/test_recover.py`

**Interfaces:**
- Consumes: `write_bundle`; `/scratch/a2rchi/bench-runs/lynx-vs-ragas/parents.json` (parent_id → parent_text); the original result files `/home/a2rchi/archi-openai-compat/bench_out/feature_matrix/<RUN_PREFIX><run>.json`.
- Produces:
  - `rebuild_question(question: dict, parents: dict[str, str], docs_root: Path, run_start: datetime, load_text: Callable[[Path], str]) -> tuple[list[str] | None, str]`. It returns `(contexts, "ok")` or `(None, reason)`, where reason is one of `"parent mismatch"`, `"loader"`, `"changed"`, `"prefix"` or `"missing file"`.
  - `recover_run(run: str) -> Path`. It writes the bundle `DATA/real/<RUN_PREFIX><run>-arm0/`, through `write_bundle(DATA / "real", RUN_PREFIX + run, rows)`, holding all recoverable rows (previously rebuilt plus recovered), and `DATA/real/<run>.ledger.jsonl` with `{key, status: "rebuilt"|"recovered"|"left_out", reason, ragas_faithfulness}`. The October faithfulness comes from the original result file.

- [ ] **Step 1: Copy the data volume read-only into `DATA/docs`.**
  - `scripts/copy_docs.sh` runs `docker run --rm -v archi-data-ragas-retrieval-ab:/d:ro busybox tar -C /d -cf - . | tar -xpf - -C $DATA/docs`, so the copy is owned by `a2rchi` and keeps mtimes.
  - Verify: `find $DATA/docs -name '*.md' | wc -l` is greater than 0.
- [ ] **Step 2: Pin the loader.**
  - Locate `load_text_from_path` with `git -C $ARCHI_PIN grep -n "def load_text_from_path"`.
  - Run `git -C $ARCHI_PIN diff --stat cd9658ba e2c1f113 -- <that file>`.
  - If it is non-empty, import it from a worktree at `cd9658ba` instead, and record which in the README.
- [ ] **Step 3: Write failing tests with a temp docs tree and a fake loader.**

```python
def test_parent_sources_use_parent_text(): ...           # stored 300 chars == parent[:300]
def test_parentless_md_recovered_as_first_4000_chars():
    ctx, why = rebuild_question(q_md_long, {}, docs, START, load_text=read_raw)
    assert why == "ok" and ctx[0] == FILE_TEXT[:4000]
def test_prefix_mismatch_left_out():        assert why == "prefix"
def test_non_md_source_left_out():           assert why == "loader"      # .py/.pdf/.c/.cpp
def test_file_newer_than_run_left_out():     assert why == "changed"
def test_one_bad_source_drops_whole_question(): ...
```

- [ ] **Step 4: Run the tests and see them fail. Step 5: Implement. Step 6: Run the tests and see them pass.**
- [ ] **Step 7: Run `recover_run` for all 5 runs.**
  - Expected: every previously rebuilt row comes back byte-identical. Compare against the old bundle rows; any difference stops the task.
  - Print the recovered and left-out counts per run, and the left-out reasons.
- [ ] **Step 8: Commit `ffj: recover parent-less questions at the known 4000-char cut`.**

### Task 4: Exact token counts

**Files:**
- Create: `$CHECK/ffj/tokens.py`, `$CHECK/scripts/count_tokens.sh`, `$CHECK/scripts/count_tokens_in_image.py`, `$CHECK/tests/test_tokens.py`

**Interfaces:**
- Produces:
  - `fits(prompt_tokens: int, limit: int) -> bool`, true when `prompt_tokens + NEW_TOKENS <= limit`.
  - `load_counts(bundle: Path, model_key: str) -> dict[str, int]`, which reads `<bundle>.tokens.<L0|L1>.json`.
  - `count_tokens.sh <bundle>... --model L0|L1`, which runs `count_tokens_in_image.py` in `$SIF` on CPU. It uses the pattern of `/scratch/a2rchi/bench-runs/lynx-vs-ragas/run_count_tokens.sh`: the binds `/scratch/a2rchi/.cache:/home/a2rchi/.cache`, `HF_HUB_OFFLINE=1` and `PYTHONNOUSERSITE=1`. It counts `build_prompt(row)` through that model's chat template with `add_special_tokens=False`.

- [ ] **Step 1: Write the failing test** `test_fits_boundary`: `fits(7400, 8000)` is true and `fits(7401, 8000)` is false.
- [ ] **Step 2: Run it and see it fail. Step 3: Implement. Step 4: Run it and see it pass.**
- [ ] **Step 5: Count `DATA/real/*` with both tokenizers.**
  - Verify: on the 425 previously rebuilt rows, the L0 counts equal the old `runs/*/tokens.json` exactly.
  - Report how many rows fit L0 and how many fit L1.
- [ ] **Step 6: Commit `ffj: exact lynx token counts per bundle`.**

### Task 5: Specific extraction

**Files:**
- Create: `$CHECK/ffj/specifics.py`, `$CHECK/tests/test_specifics.py`

**Interfaces:**
- Produces:
  - `Specific(kind: str, text: str, start: int, end: int)`. Kinds are `number`, `path`, `host`, `partition`, `flag`, `code`, `quoted`.
  - `extract_specifics(text: str) -> list[Specific]`.
  - `PARTITIONS: frozenset[str]`, the FASRC partition names that appear in the 7,006-chunk corpus. Build the set once with a grep over `parents.json` for `-p`/`--partition` values, and commit the list.

- [ ] **Step 1: Write failing tests.**

```python
def test_number_with_unit():      assert ("number", "4 TB") in kinds_texts("Each lab gets 4 TB.")
def test_flag_and_code_span():    assert {"flag", "code"} <= kinds("Add `#SBATCH --gpus=1` to the script")
def test_partition_by_name():     assert ("partition", "gpu_test") in kinds_texts("submit to gpu_test")
def test_list_markers_and_step_numbers_ignored(): assert extract_specifics("1. Log in\nStep 2: run it") == []
def test_code_fence_kept_as_one_code_specific():  # Review Focus 1
    s = extract_specifics("```\nsbatch job.sh\nsqueue -u me\n```")
    assert [x.kind for x in s] == ["code"]
def test_hostname_and_path(): ...                  # login.rc.fas.harvard.edu, /n/holylabs/LABS/x
```

- [ ] **Step 2: Run them and see them fail. Step 3: Implement with regexes; one function per kind. Step 4: Run them and see them pass. Step 5: Commit `ffj: specific extraction`.**

### Task 6: Base rows, cleanliness and the support ledger

**Files:**
- Create: `$CHECK/ffj/base_rows.py`, `$CHECK/material/support_ledger.jsonl` (authored), `$CHECK/tests/test_base_rows.py`

**Interfaces:**
- Consumes: `read_rows`, `extract_specifics`, `load_counts`, the ledger's `ragas_faithfulness`.
- Produces:
  - `occurrence_missing(row: dict) -> list[Specific]`, the specifics that do not occur, case-insensitively, in the joined contexts.
  - `verify_support(row: dict, entries: list[dict]) -> list[str]`. It returns problems. An entry is `{run, key, specific, quote}`. A problem is raised when a specific has no entry, when the quote is not a verbatim substring of the contexts, or when the quote does not contain the specific.
  - `candidate_base_rows() -> list[tuple[str, str]]`, the (run, key) pairs with RAGAS ≥ 0.8 and no missing occurrence.
  - `base_rows() -> list[tuple[str, str]]`, the candidates whose `verify_support` returns no problems.

- [ ] **Step 1: Write failing tests.**
  - `test_occurrence_missing_finds_absent_value`.
  - `test_verify_support_rejects_quote_not_in_documents`.
  - `test_verify_support_rejects_quote_without_specific`.
  - `test_verify_support_requires_every_specific`.
  - `test_candidates_require_ragas_08`.
- [ ] **Step 2: Run them and see them fail. Step 3: Implement. Step 4: Run them and see them pass.**
- [ ] **Step 5: Author `support_ledger.jsonl` for every candidate (agent step).**
  - For each specific, quote the document sentence that ties it to the same thing the answer ties it to: the same partition, command or user group.
  - When no such sentence exists, omit the entry. The row then fails, by design.
  - Then run `base_rows()` and print the counts: total, short (fits L0) and distinct questions.
- [ ] **Step 6: Commit `ffj: base rows with occurrence check and support ledger`.**

### Task 7: Planted-error set B and the dev planted items

**Files:**
- Create: `$CHECK/ffj/planted.py`, `$CHECK/material/invented_steps.txt`, `$CHECK/material/general_advice.txt`, `$CHECK/material/paraphrases.jsonl` (authored), `$CHECK/tests/test_planted.py`

**Interfaces:**
- Consumes: `base_rows`, `extract_specifics`, `PARTITIONS`, `write_bundle`, `load_counts`.
- Produces:
  - `edit_number(sentence, spec, rng) -> str`, `edit_path_host_partition(...)`, `edit_flag(...)`, `edit_policy(sentence, rng) -> str`, `add_invented_step(answer, rng) -> str` and `add_general_advice(answer, rng) -> str`.
  - `verify_harmful(row, original, edited) -> str | None` and `verify_benign(row, original, edited) -> str | None`. Each returns a rejection reason or None.
  - `build_planted() -> Path`. It writes bundle `DATA/planted/ffj-planted-arm0` (70 items) and `DATA/planted/ffj-planted-dev-arm0` (4 items), plus `material/planted_ledger.jsonl` with `{item, base_run, base_key, kind: harmful|benign, type, original, edited, short: bool}`.
  - Item keys are opaque (`p001`…). The ledger maps them back.

- [ ] **Step 1: Write the two fixed lists.**
  - `invented_steps.txt`: 16 plausible FASRC commands or steps that do not occur in the corpus. Grep each against `parents.json` and require 0 hits.
  - `general_advice.txt`: 20 sentences with no specifics. Assert `extract_specifics(line) == []` for each.
- [ ] **Step 2: Write failing tests.**

```python
def test_number_edit_changes_value_not_unit(): ...
def test_policy_edit_flips_negation(): ...                 # "can" -> "cannot", "required" -> "optional"
def test_reject_when_new_value_in_documents():            # Review Focus 5
    assert verify_harmful(row, s, s.replace("4 TB", "8 TB")) == "new value in documents"   # docs mention 8 TB
def test_reject_when_target_occurs_twice_in_answer():      # Review Focus 5
    assert verify_harmful(row_twice, s, edited) == "target repeated"
def test_benign_advice_has_no_specifics(): assert verify_benign(row, a, add_general_advice(a, rng)) is None
def test_selection_counts_and_short_floors(tmp_data):
    led = ledger(build_planted())
    assert Counter(r["type"] for r in led if r["kind"] == "harmful") == {t: 8 for t in (1, 2, 3, 4, 5)}
    assert short_harmful(led) >= 10 and short_benign(led) >= 8
def test_deterministic_with_seed(): ...
```

- [ ] **Step 3: Run them and see them fail. Step 4: Implement. Step 5: Run them and see them pass.**
- [ ] **Step 6: Author `paraphrases.jsonl`.** That is 15 paraphrases plus 1 for the dev set, each restating one sentence with the same specifics. `verify_benign` must pass on each.
- [ ] **Step 7: Run `build_planted()`. Count tokens for both bundles (Task 4), and confirm every `short: true` item fits L0.** Replace any that doesn't, using the same seed stream. Print the achieved short split.
- [ ] **Step 8: Commit `ffj: planted-error set and dev items`.**

### Task 8: Unrelated-answer control C

**Files:**
- Create: `$CHECK/ffj/control.py`, `$CHECK/tests/test_control.py`

**Interfaces:**
- Consumes: the `012746` real bundle and `load_counts`.
- Produces:
  - `shares_passage(a: dict, b: dict) -> bool`, which compares passage text after stripping whitespace.
  - `build_control() -> Path`. It writes `DATA/control/ffj-control-arm0` (20 items, keys `c01`…) and `material/control_ledger.jsonl` with `{item, target_key, donor_key, short}`. It also exports the old far-swap set to `DATA/control/ffj-control-old-arm0` for the comparability row.

- [ ] **Step 1: Write failing tests.**
  - `test_shares_passage_by_text_not_position`, which covers Review Focus 2.
  - `test_twenty_distinct_donors_none_sharing`.
  - `test_ten_short_ten_long_after_swap`, using counts recomputed on the swapped rows.
  - `test_deterministic_with_seed`.
- [ ] **Step 2: Run them and see them fail. Step 3: Implement. Step 4: Run them and see them pass. Step 5: Count tokens and confirm the split. Step 6: Commit `ffj: rebuilt unrelated-answer control`.**

### Task 9: Lynx variant inputs (L0t, L0s, L1s)

**Files:**
- Create: `$CHECK/ffj/lynx_variants.py`, `$CHECK/tests/test_lynx_variants.py`

**Interfaces:**
- Consumes: `read_rows`, `write_bundle`, `fits`, and a `count: Callable[[dict], int]` injected so the tests can avoid a tokenizer.
- Produces:
  - `bm25_scores(query: str, passages: list[str]) -> list[float]`, using k1 1.5 and b 0.75 over lowercase word tokens.
  - `trim_row(row, count, limit=8000, k=3) -> tuple[dict, int]`. It returns the trimmed row and the number of passages kept. Passages are deduplicated by text, ranked by BM25 against `row["response"]`, the top k are kept in their original order, and k drops to 2, then 1, until the row fits.
  - `split_units(answer: str) -> list[str]`. A unit is a sentence, a bullet or a whole fenced code block. Headings and units under 20 characters are dropped.
  - `unit_rows(row, mode: "L0s"|"L1s", count) -> list[dict]`. Keys are `<key>#u<NN>`, the response is the unit, and contexts are the unit's own top 3 (L0s) or all passages (L1s).
  - `rollup(unit_verdicts: list[str|None]) -> str|None`: FAIL if any unit is FAIL, PASS if all are PASS, otherwise None.
  - `build_variants(src_bundle: Path) -> dict[str, Path]`, which writes `<name>.L0t`, `<name>.L0s` and `<name>.L1s` bundles beside the source, plus a `units.jsonl` map.

- [ ] **Step 1: Write failing tests.**

```python
def test_trim_keeps_top3_in_original_order(): ...
def test_trim_dedups_repeated_passage(): ...              # Review Focus 2
def test_trim_falls_back_to_two_then_one(): ...
def test_split_keeps_code_fence_whole_and_drops_headings(): # Review Focus 1
    assert split_units("## Steps\n1. Log in to the cluster now.\n```\nsbatch a.sh\nsqueue\n```") == ["1. Log in to the cluster now.", "```\nsbatch a.sh\nsqueue\n```"]
def test_rollup():
    assert rollup(["PASS", "FAIL"]) == "FAIL" and rollup(["PASS", "PASS"]) == "PASS" and rollup(["PASS", None]) is None
def test_unit_keys_unique_and_reversible(): ...
```

- [ ] **Step 2: Run them and see them fail. Step 3: Implement. Step 4: Run them and see them pass.**
- [ ] **Step 5: Build the variants for every real, planted and control bundle, and count tokens.**
  - Coverage for L0t, L0s and L1s is the share of real rows whose prompts all fit.
  - Verify: L0t fits ≥ 90% of real rows; otherwise stop and report, because the spec's coverage bar would reject L0t.
- [ ] **Step 6: Commit `ffj: lynx trimmed and per-unit inputs`.**

### Task 10: Human sample D, top-up and the dev split

**Files:**
- Create: `$CHECK/ffj/samples.py`, `$CHECK/tests/test_samples.py`

**Interfaces:**
- Consumes: the real-row ledgers (RAGAS scores), token counts, `base_rows`, and `planted_ledger.jsonl`.
- Produces:
  - `draw_d() -> list[dict]`. Each record is `{record_id, source: "real"|"planted", run, key, item}`, and there are 46 of them, shuffled.
  - `draw_topup(already: set[str]) -> list[dict]`, 20 rows from RAGAS < 0.5 with questions not already in D.
  - `draw_dev() -> list[dict]`, 10 rows from `130451`.
  - All three are written to `material/d_records.jsonl`, `material/topup_records.jsonl` and `material/dev_records.jsonl`.

- [ ] **Step 1: Write failing tests.**
  - `test_strata_16_12_12`.
  - `test_one_row_per_question`.
  - `test_top_stratum_from_base_rows`.
  - `test_hidden_items_share_no_question_with_real_rows`.
  - `test_dev_rows_not_in_d`.
  - `test_topup_questions_new`.
  - `test_deterministic_with_seed`.
- [ ] **Step 2: Run them and see them fail. Step 3: Implement (only October RAGAS scores, token counts and the base-row list are read; no judge output). Step 4: Run them and see them pass. Step 5: Commit `ffj: human sample, top-up and dev split`.**

### Task 11: LLM judges (S, mode a and mode b) and the frozen rule prompt

**Files:**
- Create: `$CHECK/ffj/llm_judges.py`, `$CHECK/ffj/rule_prompt.md`, `$CHECK/scripts/score_llm.py`, `$CHECK/tests/test_llm_judges.py`

**Interfaces:**
- Consumes:
  - `src.utils.ragas_scoring.build_ragas_scorer(*, enabled_metrics, ragas_settings, verbosity, judge_factory, embeddings_factory, judge_provider, judge_model, on_call=None)`. `ragas_settings` must contain `batch_size`. The function `score_fn(metric, rows_without_key) -> list[float|nan]`.
  - `build_ragas_judge_llm(identity, *, chat_openai, get_model)` with `get_model` from `src.archi.providers` for S (needs `HUIT_API_KEY`).
  - `build_ragas_embeddings` with `HuggingFace`.
  - `src.utils.judge_scorer.read_checkpoint`, `append_records`.
- Produces:
  - `openrouter_llm(model: str, host: str) -> ChatOpenAI`.
  - `rule_verdict(llm, row) -> dict`. It returns `{verdict: "PASS"|"FAIL"|None, specific: str|None, usage, raw}`.
  - `parse_rule_output(text: str) -> tuple[str|None, str|None]`.
  - `score_bundle(bundle: Path, judge: str, out: Path) -> None`, where judge is one of `S`, `Ga`, `Na`, `Ma`, `Gb`, `Nb`, `Mb`. It writes `out/<bundle>.<judge>.jsonl`, one record per key: `{key, value|verdict, error, usage, provider_host, ts}`.
  - `scripts/score_llm.py <judge> <bundle>...`.

- [ ] **Step 1: Pick the OpenRouter hosts and record them in `config.OR_HOSTS`.**
  - Run `curl -s https://openrouter.ai/api/v1/models/<id>/endpoints` for each model.
  - Pick a full-precision host (bf16/fp16) with context ≥ 128k and uptime ≥ 99%. The free Nemotron model has only the Nvidia host.
  - Check the October RAGAS version: `docker run --rm --entrypoint pip <the benchmarking-ragas-retrieval-ab:new image> show ragas` must print `0.3.5`, the host env's version. If it does not, S and mode a run inside that image instead; record which in the README.
- [ ] **Step 2: Write failing tests, using a fake LLM.**

```python
def test_parse_rule_output_pass_fail_and_specific(): ...
def test_empty_content_or_length_cutoff_is_no_verdict_after_retries():   # Review Focus 3
    llm = FakeLLM([Msg(content="", finish="stop"), Msg(content='{"verdict": "PA', finish="length")] * 4)
    assert rule_verdict(llm, row)["verdict"] is None and llm.calls == 4
def test_daily_cap_waits_and_resume_skips_done(tmp_path):                 # Review Focus 4
    # first run: 3 keys done then a 429 "free-models-per-day"; second run resumes
    assert keys_in(out) == ALL_KEYS and no_duplicate_keys(out) and not any(r.get("verdict") for r in capped)
def test_openrouter_llm_pins_host():
    assert openrouter_llm("openai/gpt-oss-120b", "deepinfra").extra_body == {"provider": {"order": ["deepinfra"], "allow_fallbacks": False}}
def test_mode_a_uses_october_settings(monkeypatch): ...   # captures ragas_settings timeout 600, max_workers 6
def test_no_key_in_outputs(tmp_path): ...
```

- [ ] **Step 3: Run them and see them fail. Step 4: Implement.**
  - Retries: up to 3, with backoff on transport errors, timeouts and 429s.
  - A 429 whose body names the free-model daily limit waits until 00:00 UTC; it is not a retry.
- [ ] **Step 5: Run the tests and see them pass.**
- [ ] **Step 6: Tune `rule_prompt.md` on the dev split only.**
  - The prompt contains the spec's grading rule verbatim and asks for JSON `{"verdict": "PASS"|"FAIL", "specific": <text or null>}`.
  - The builder labels the 10 dev rows by the rule in `material/dev_labels.jsonl` before running.
  - Score only `material/dev_records.jsonl` plus `ffj-planted-dev` with Gb, Nb and Mb.
  - Stop when each model's output parses on all 14 items. Agreement is reported but not tuned to a target.
- [ ] **Step 7: Write `sha256(rule_prompt.md)` into `material/FROZEN_PROMPT.sha256`. Commit `ffj: llm judges and frozen rule prompt`.**

### Task 12: Argilla dataset and export

**Files:**
- Create: `$CHECK/ffj/argilla_io.py`, `$CHECK/material/guidelines.md`, `$CHECK/tests/test_argilla_io.py`

**Interfaces:**
- Consumes: `d_records.jsonl`, the real and planted rows. Argilla 2.8 SDK: before writing, pull the current docs via Context7 (`argilla-io/argilla`), as the project memory requires. The URL is `http://localhost:3080`, and the key comes from `~/.archi/secrets/argilla_api_key.txt`.
- Produces:
  - `record_fields(row) -> dict`. It returns exactly `question`, `answer` and `documents`, with documents joined by `\n\n---\n\n`. No run, model, code version, score or verdict appears in it.
  - `dataset_settings() -> rg.Settings`. Fields are markdown text. Questions:
    - `made_up_or_wrong`: label, `No` / `Yes` / `Can't tell`, required.
    - `first_specific`: text, optional in Argilla, but the export flags a "Yes" without one.
    - `how_much_backed`: label, `all` / `most` / `about half` / `little`, optional.
    - `guidelines` = `guidelines.md`.
  - `load_records(name: str, records: list[dict]) -> None`.
  - `export(name: str) -> list[dict]`, giving `{record_id, made_up_or_wrong, first_specific, how_much_backed, status}`. It uses its own loop, because `pull_grades_from_argilla` drops unknown question names.

- [ ] **Step 1: Write `guidelines.md`.** It holds the spec's rule verbatim, one failure example and one general-advice pass example, both written from rows outside D.
- [ ] **Step 2: Write failing tests.**
  - `test_fields_hide_judge_and_run_info`.
  - `test_record_ids_opaque`.
  - `test_export_maps_all_three_questions`, using a fake dataset object.
  - `test_export_flags_yes_without_specific`.
- [ ] **Step 3: Run them and see them fail. Step 4: Implement. Step 5: Run them and see them pass. Step 6: Commit `ffj: argilla load and export`.** Loading happens after the material lock (Task 14).

### Task 13: Material lock

**Files:**
- Create: `$CHECK/scripts/material_lock.py`, `$CHECK/MATERIAL_LOCK.json`, `$CHECK/tests/test_material_lock.py`

**Interfaces:**
- Produces:
  - `build_lock() -> dict`. It contains `{design_lock, archi_pin, bench_commit_parent, files: {relpath: sha256}, bundles: {path: rows_sha256}, counts: {...}}`. It covers every file in `material/`, `rule_prompt.md`, `FROZEN_PROMPT.sha256` and `config.py`, and every bundle under `DATA` (real, planted, control, the variants and dev).
  - `verify_lock(lock) -> list[str]`, which returns any mismatches.

- [ ] **Step 1: Write the failing test** `test_verify_detects_changed_material_file`, then implement, then pass it.
- [ ] **Step 2: Write `MATERIAL_LOCK.json`, run `verify_lock` (expect `[]`), and commit `ffj: material lock`.**
  - Ask the operator to approve a push of branch `ffj-2026-10` to `fasrc/archi-bench-out`. The pushed commit is the timestamp the spec requires before scoring.

### Task 14: Scoring runs and grading (runbook)

**Files:**
- Create: `$CHECK/scripts/run_lynx.sh`, `$CHECK/scripts/check_complete.py`, `$CHECK/tests/test_check_complete.py`

**Interfaces:**
- Consumes:
  - The Lynx CLI `python3.12 -m scripts.benchmarking.lynx.score <bundle> --model <id> --revision <rev> --out-dir <dir> --context-tokens <N>`, run inside `$SIF` with `--pwd $ARCHI_PIN`.
  - v1.1 goes through `/scratch/a2rchi/bench-runs/lynx-vs-ragas/v11/run_scorer_v100.py` (`GPU_UTIL=0.85`) with `VLLM_FLASH_V100_DISABLE_PAGED_PREFILL=1`, and `CUDA_VISIBLE_DEVICES` is 0 or 1.
  - The output refuses to overwrite, so every run gets a new out-dir under `DATA/scores/<judge>/`.
- Produces: `check_complete(judge: str, bundles: list[Path]) -> dict`, giving per-bundle `{expected, scored, no_verdict, missing}`.

- [ ] **Step 1: Write and pass `test_check_complete_counts_missing_and_no_verdict`. Commit.**
- [ ] **Step 2: Verify the lock before every session of runs:** `verify_lock` returns `[]`.
- [ ] **Step 3: Run the early stop.** Score C with L0t, L0s (short items only for L0 variants), L1 and L1s. Any variant flagging < 16 of 20 is recorded as rejected in `DATA/scores/early_stop.json` and skipped below.
- [ ] **Step 4: Run the Lynx judges.**
  - L0: B-short and C-short.
  - L1: B and C. Its real-row verdicts are re-read from the recovered bundles, so the rows recovered in Task 3 are scored too.
  - Surviving variants: A, B and C.
- [ ] **Step 5: Run the OpenRouter judges.**
  - Ga, Ma, Gb and Mb score A, B and C.
  - Na and Nb score the same, within the free cap, about 2 days.
  - Watch spend with `curl -s https://openrouter.ai/api/v1/credits -H "Authorization: Bearer $KEY"`. Stop and ask the operator if usage passes $15.
- [ ] **Step 6: Run S.** It scores B, C and the original of every B base row (fresh, same session, for the paired reading), plus 20 seeded real rows for the repeatability check.
- [ ] **Step 7: Load D into Argilla (Task 12) and tell the operator it is ready.**
  - When they finish: export, count the "Yes" grades, and if there are fewer than 8, load the top-up and wait again.
  - After D, apply the contamination exclusion. Record the exclusions in `DATA/scores/exclusions.json`.
- [ ] **Step 8: Run `check_complete` for every judge × bundle.** Expected: no `missing`. Commit the small outputs (`DATA/scores/**/summary` and the ledgers) into `$CHECK/results/`.
  - Hard deadline: the material-lock date + 14 days. If D is unfinished then, analyze with the graded rows and flag the shortfall (spec "Stopping rule").

### Task 15: Analysis and decision

**Files:**
- Create: `$CHECK/ffj/analyze.py`, `$CHECK/scripts/analyze.py`, `$CHECK/tests/test_analyze.py`

**Interfaces:**
- Consumes: all score files, `planted_ledger`, `control_ledger`, the D export, exclusions, the real-row ledgers, and `scripts.benchmarking.paired_tests.paired_binary` for the arm-level McNemar tests.
- Produces:
  - `flags(judge, item_scores, originals) -> dict[str, bool|None]`. Binary judges flag on FAIL. Score judges on B use the paired drop ≥ `PAIRED_MARGIN`. Score judges on C and D use the chosen cutoff.
  - `choose_cutoff(judge_scores_d, human_d) -> float`, the higher balanced accuracy of `CUTOFFS`.
  - `metrics(judge) -> dict`, which computes catch rate, real-error catch rate, false-alarm rate (B half, D half and pooled), unrelated flagged out of 20, verdict share of the 130 decision items, and coverage.
  - `decide(m: dict, s: dict, yes_count: int) -> str`, one of `adopt`, `reject` or `inconclusive`, applying every bar, including the real-error bands 8+ / 4–7 / < 4.
  - `results.json` holds every judge's metrics and decision, plus these secondary keys (spec "Secondary analyses"):
    - `per_type_catch` and `per_length_catch`.
    - `paired_b_binary`.
    - `margin_sensitivity`, at 0.02, 0.05 and 0.10.
    - `human_vs_judge`: agreement, kappa and the rows that differ.
    - `human_hidden_catch`.
    - `arm_level`: new − old on the 72-question set and on the full recovered set, and Qwen3.8 vs Qwen3.6 on 52 questions. Question bootstrap with 10,000 resamples and seed `SEED`; McNemar via `paired_binary`.
    - `continuity`: mode a vs S, as Spearman, mean shift and 0.5-crossings.
    - `s_repeatability`, on the 20 rows.
    - `cost`: tokens and $ per item per judge, and S's faithfulness-only cost per 109-question run next to October's 5-metric `judge_usage`.
    - `trim_check`: L0t vs L0 on the 69 rows that fit in full.
    - `unit_failures`: which unit failed, and whether it holds the planted edit or the specific the human pasted.
    - `recovery_counts`.

- [ ] **Step 1: Write failing tests.**

```python
def test_paired_flag_uses_margin():          assert flags_b(score_orig=0.85, score_planted=0.80) is True and flags_b(0.85, 0.81) is False
def test_no_verdict_counts_against_both_ways(): ...  # miss on harmful, false alarm on clean; no original score -> no verdict
def test_cant_tell_excluded_and_counted(): ...
def test_contamination_exclusion_applies_to_all_judges(): ...
def test_decide_thresholds_inclusive():
    assert decide(m(catch=0.80, fa=0.20, unrel=18, verdict=0.95, cov=0.90, real=0.60), s=m(catch=0.85, fa=0.30), yes_count=8) == "adopt"
def test_real_error_bands():
    assert decide(..., real_caught=0, yes_count=5) == "reject" and decide(..., real_caught=3, yes_count=5) == "adopt"
    assert decide(..., real_caught=0, yes_count=3) != "reject"     # waived below 4
def test_l0_never_adopted_coverage(): ...
```

- [ ] **Step 2: Run them and see them fail. Step 3: Implement. Step 4: Run them and see them pass.**
- [ ] **Step 5: Run `scripts/analyze.py` → `results/results.json`, and print the decision table. Commit `ffj: analysis and decision`.**

### Task 16: Report

**Files:**
- Create: `$CHECK/build_report.R`, `$CHECK/REPORT.md`, `$CHECK/figures/`, `$CHECK/page/`

- [ ] **Step 1: Invoke the `archi-bench-report-format` skill and follow it.**
  - R recomputes every number from the score files and the ledgers.
  - It stops on any mismatch with `results.json`.
  - The report has the decision table first, a "what is measured" line per judge, a per-item ledger, and Takeaway / Read-with-care.
- [ ] **Step 2: Run `$RSCRIPT build_report.R`. Expected: exits 0 with every cross-check passing. Commit `ffj: report`, and ask before pushing.**
- [ ] **Step 3: Draft (do not post) comments for #583 and #737 summarizing the decision. Ask the operator before posting.**

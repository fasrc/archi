# Feature-matrix benchmark campaign (2026-09)

**Status:** adopted 2026-09-03 (operator decision, recorded on
[#396](https://github.com/fasrc/archi/issues/396)). This document is the campaign plan
for issue #396 and the runbook for every arm. It is complete when every arm in
[§3](#3-arms) has a row in [§9](#9-results-ledger) and every feature carries a verdict
from [§4.4](#44-the-verdict-rule).

Read [Interpreting Benchmark Results](../interpreting_benchmark_results.md) first. Its
gate G1–G8 applies to every verdict below, and its procedures are the reference for the
tool that computes them.

## 1. Purpose and gate

The v2026.10.0 release ("measurably better answers") claims measured improvement while
most of the retrieval and ingest configuration is set by inherited default. #396 closes
that gap: one baseline arm plus one arm per toggle, each with a before/after comparison on
four metric families. The four families and where each comes from:

| Family | Metric(s) | Source |
|---|---|---|
| Accuracy, RAGAS + sources | `answer_relevancy`, `faithfulness`, `context_precision`, `context_recall`, `answer_correctness`, `source_accuracy` | `archi evaluate` (the RAGAS harness) |
| Accuracy, gold atoms | atom score, required-atom recall, pass rate | `archi eval qa` (the QA evaluator, CLI form) |
| Time to result | per-question `time_elapsed` (harness), per-attempt `duration_ms` (QA) | both, on the same stack |
| Time to ingest | `ingest_wall_seconds` per arm | `archi evaluate`, after [#417](https://github.com/fasrc/archi/issues/417) |

**Gate for a verdict.** A feature verdict is valid only when G1–G8 hold: a committed
pre-registration (`docs/eval/preregs/2026-09-feature-matrix.md`),
a measured noise floor, one pinned corpus per retrieval comparison, one bank, paired
per-question comparison over rows scored in both arms, an effect above both 2·SE and 2·σ,
and unbroken anchors. `compare_runs.py` ([#419](https://github.com/fasrc/archi/issues/419))
enforces G3–G8 and refuses to print a verdict otherwise.

## 2. Fixed factors

Everything below is identical for every arm and every run. Each value is pinned by a hash
that the ledger records per run.

| Factor | Value | Pin |
|---|---|---|
| Question bank | `config/benchmarking/fasrc_ragas_queries.json` (archi-config), 105 rows, all `status: draft` | git blob `99efd5b4d4f37dc696476be0f82113251987dc45`, sha256 `a116bd72…d4156` |
| Anchors | `examples/benchmarking/anchor_questions.json`, 5 rows; one `should_refuse` row duplicates a bank row, so 109 questions are asked | sha256 `6b2fd991…0311e` |
| Agent prompt | `config/agents/claw/fasrc-docs.md` — byte-identical to the GPU host's production spec (`deploy/fasrc-dev/agents/fasrc-docs.md`). **Made true on 2026-09-04**: the host previously served `fasrc-v2.md` (name `FASRC`, `search_vectorstore_hybrid` only), which was retired to `agents/archive/`; production now serves this file, verified by a live cited answer | sha256 `ac22702a…4ce8` |
| System under test | vLLM `palmfuture/Qwen3.6-35B-A3B-GPTQ-Int4` at `http://archi.rc.fas.harvard.edu:8001/v1`, `temperature: 0.3`, `enable_thinking: false`, `context_window: 32768`. **The 32768 was not actually configured until 2026-09-04:** every arm YAML omitted `services.chat_app.context_editing`, which `base-config.yaml` emits only when declared, so the arms would have run with NO in-loop bound (`No in-loop context limit installed`, observed in the smoke) while production ships 32768. The block was added to all nine arms pre-lock, matching production byte for byte | recorded in `config_version.key_settings`; the lock's `sut.context_editing` is the check — it read `null` before the fix |
| Judge, both evaluators | HUIT Bedrock `us.anthropic.claude-sonnet-4-5-20250929-v1:0`, timeout 300 s | recorded per artifact / QA profile snapshot |
| Embedding | `HuggingFaceEmbeddings` → `sentence-transformers/all-MiniLM-L6-v2` | template default |
| Sources | `config/lists/sources.list`, 152 non-comment lines | sha256 `8d4590f4…b787f` |
| Code | one `dev` SHA for the whole campaign; `/home/a2rchi/archi-openai-compat` is pulled to it before arm 00 | `metadata.code_version.digest`, identical on every artifact |
| Host | **holygpu7c0717** (`archi.rc.fas.harvard.edu`), the production `dev` GPU host — operator decision 2026-09-04, a deviation from the original claw plan (§10). Every stack runs `--hostmode` on Postgres 5434 and data-manager 7882, which are free here; the production `dev` stack (7861) and both vLLM servers (8001/8002) stay up alongside | — |

**Why this prompt.** Every goldenset run before this campaign used
`config/agents/fasrc-inline-v1.md`. Its body is the same text, but its tool list lacks
`search_metadata_index`, which production ships. Those runs measured an agent production
does not run. Prior artifacts are therefore not a baseline for this campaign.

**Why the QA evaluator uses the same judge.** The atoms judge and the RAGAS judge are one
model, so a disagreement between the two families is a difference of method, not of
grader.

## 3. Arms

One factor flips per arm. Every arm YAML is `00-baseline.yaml` plus one key; arm 02 is
the exception (two keys) because `character` chunks carry no `parent_id`, so the rerank
retriever has nothing to return (template comment, `base-config.yaml` → `chunking`).
Files live in archi-config at `config/benchmarking/feature_matrix/`.

| Arm | File | Flipped key (under `data_manager`) | Corpus | Stack | RAGAS runs | QA runs |
|---|---|---|---|---|---|---|
| 00 | `00-baseline.yaml` | none — shipped defaults made explicit: `chunking.strategy: sentence`, `retrievers.hierarchical_rerank.enabled: true`, `processing.html_to_markdown.enabled: true`, `processing.categorization.enabled: true`, `stemming.enabled: false`, k = 5 | reference | `fm-00` | 3 opening + 1 closing | 1 + 1 |
| 01 | `01-rerank-off.yaml` | `retrievers.hierarchical_rerank.enabled: false` | = 00 | `fm-00`, re-seeded | 2 | 1 |
| 02 | `02-chunking-character.yaml` | `chunking.strategy: character` + `retrievers.hierarchical_rerank.enabled: false` | new | `fm-02` | 2 | 1 |
| 03 | `03-categorization-off.yaml` | `processing.categorization.enabled: false` | new | `fm-03` | 2 | 1 |
| 04 | `04-stemming-on.yaml` | `stemming.enabled: true` | new | `fm-04` | 2 | 1 |
| 05a | `05a-k3.yaml` | `retrievers.hierarchical_rerank.num_documents_to_retrieve: 3` | = 00 | `fm-00`, re-seeded | 2 | 1 |
| 05b | `05b-k8.yaml` | `retrievers.hierarchical_rerank.num_documents_to_retrieve: 8` | = 00 | `fm-00`, re-seeded | 2 | 1 |
| 06 | `06-html-to-markdown-off.yaml` | `processing.html_to_markdown.enabled: false` | new | `fm-06` | 2 | 1 |
| 07 | `07-chunking-markdown.yaml` | `chunking.strategy: markdown` | new | `fm-07` | 2 | 1 |

Not in this campaign: `chunking.chunk_overlap` (the key does not exist until
[#403](https://github.com/fasrc/archi/issues/403) lands; sweep 20/64/128 then),
embedding models ([#216](https://github.com/fasrc/archi/issues/216) owns that axis and
this campaign consumes its winner as a later baseline), interaction effects, prompt
variants.

## 4. Statistical power

This section is the "first task" #396 asks for. Every number here comes from a command in
[Appendix A](#appendix-a-how-to-re-derive-the-numbers).

**The numbers in §4.1–4.2 are a planning prior, not this campaign's noise floor.** They
come from runs that used `fasrc-inline-v1.md`, a different tool surface from the campaign
prompt (§2). They size the budget. The σ that every verdict uses is measured on this
campaign's own prompt, corpus and code by the three opening baseline runs (§6 step 4,
Procedure A), and `compare_runs.py` computes every MDE from those runs — never from the
tables below. If the opening σ exceeds the prior by more than 50 % on the primary metric,
the operator decides, before any arm runs, whether to raise N (and re-locks the pre-reg).

### 4.1 Run-to-run noise of the mean (σ) — prior

Six goldenset runs of the same code on the same bank (2026-08-11 ×3, 2026-08-17 ×3), on
the `fasrc-inline-v1.md` prompt:

| Metric | mean | σ of run means | 2·σ |
|---|---|---|---|
| answer_relevancy | 0.693 | 0.025 | 0.049 |
| faithfulness | 0.574 | 0.027 | 0.053 |
| context_precision | 0.599 | 0.016 | 0.031 |
| context_recall | 0.839 | 0.021 | 0.043 |
| answer_correctness | 0.465 | ≈ 0.009 (three same-code values from the #305 bracket) | ≈ 0.018 |

### 4.2 Paired per-question sensitivity (SE) — prior

The three same-code pairs of 2026-08-17, paired on question text over rows scored in both
runs (n = 108–109), same prompt caveat as above:

| Metric | SD of per-question delta | SE at n = 109 | 2·SE |
|---|---|---|---|
| answer_relevancy | 0.23 | 0.022 | 0.045 |
| faithfulness | 0.24–0.28 | 0.023–0.027 | 0.047–0.054 |
| context_precision | 0.05–0.13 | 0.005–0.012 | 0.010–0.025 |
| context_recall | 0.11–0.18 | 0.011–0.017 | 0.021–0.034 |

### 4.3 Runs per arm and the minimum detectable effect

- **N = 2 RAGAS runs per arm**, averaged; the baseline gets 3 opening runs (σ for this
  campaign's own corpus and code, Procedure A) and 1 closing run (drift, [§6](#6-order)).
- With N = 2 on each side the pooled per-question SE shrinks by about 1/√2 and the
  run-mean σ likewise. The minimum detectable effect (MDE) per metric is
  `max(2·SE_pooled, 2·σ_pooled)`, computed by `compare_runs.py` from the runs actually made
  and printed next to every delta.
- **What this campaign expects to be able to claim (prior):** deltas of about **0.03 or
  more** on `context_precision`, `context_recall` and `answer_correctness`; about **0.05
  or more** on `answer_relevancy` and `faithfulness`. The locked MDE per metric is the
  one computed from the opening baseline runs and printed by `compare_runs.py`. A true
  effect below it is out of reach at this budget and is reported as "no measurable
  difference at N = 2 (MDE = x)", never as a direction.
- **Count metrics are cheaper.** Source hits, `should_refuse` passes, degraded rows, and
  QA pass counts are paired binary outcomes; an exact paired test (McNemar) on 109 rows
  decides them at N = 1, as the August 2026 overflow-apology count did (11 → 0,
  p = 8.1e-5).
- **QA atoms.** One attempt per question per arm. Pass rate and atom score are reported
  with their denominators; a QA verdict follows the same rule as the RAGAS one, with the
  QA run's own paired deltas.

### 4.4 The verdict rule

For each feature (arm vs. baseline), in this order:

1. **Void checks** ([§7](#7-invariants-the-compare-step-enforces)) — if any fails, the arm
   is void and is rerun or recorded as failed. No numbers are reported from a void arm.
2. **Primary metric** (fixed in the pre-registration): `context_precision` for every arm
   except 04 (stemming), whose primary is `context_recall`. `helps` if the paired delta is
   positive and above the MDE; `hurts` if negative and below −MDE; else `no measurable
   difference`.
3. **G8 guard** — a `helps` verdict is downgraded to `mixed` if any other RAGAS metric,
   `source_accuracy`, or the QA pass rate regresses by more than one σ, or if an
   `easy_retrieve` anchor drops by more than σ, or the `should_refuse` anchor fails.
4. **Cost side**, always reported next to the verdict: Δ time to ingest, Δ chunk count
   (index size), Δ warm p90 time to result, Δ degraded-row count.
5. **Default disposition**: a shipped default the campaign shows to `hurt` gets a follow-up
   issue filed against it; `no measurable difference` on a default that costs ingest time
   or latency is itself a finding and gets an issue proposing the cheaper setting.

## 5. Protocol per arm

All commands run from `/home/a2rchi/archi-openai-compat` (so `config/...` and `examples/...` resolve),
with `~/miniforge3/envs/archi` on `PATH`, the FASRC VPN up, and the judge key in the env
file: `RAGAS_ENV_FILE=/home/a2rchi/.archi/.env.benchmark`. The wrappers under
`scripts/benchmarking/feature_matrix/` are one step each; the runbook is this section.

### 5.1 Ingest arm (00, 02, 03, 04, 06, 07)

```bash
ARM=03; YAML=config/benchmarking/feature_matrix/03-categorization-off.yaml
scripts/benchmarking/feature_matrix/run_arm.sh $ARM $YAML            # archi evaluate -n fm-$ARM -c $YAML --hostmode
#  → deploys postgres (5434) + data-manager (7882) + benchmarking; ingests; runs 109 questions; scores
scripts/benchmarking/feature_matrix/archive_run.sh $ARM 1 $YAML --wait  # proves the artifact ran this arm on one corpus; appends ledger.json, writes the corpus pin
scripts/benchmarking/feature_matrix/run_arm.sh $ARM --rerun          # benchmark container only, same corpus → run 2
scripts/benchmarking/feature_matrix/archive_run.sh $ARM 2 $YAML
scripts/benchmarking/feature_matrix/qa_arm.sh $ARM $YAML             # archi eval qa against the same stack (§5.3)
archi delete --name fm-$ARM --rmi --rmv                              # after archive_run.sh confirmed both artifacts
```

**Arm 00 is the exception to this block.** The opening baseline gets THREE RAGAS runs
(`run_arm.sh 00 --rerun` and `archive_run.sh 00 3 …` once more after run 2) because those
three runs are the campaign's σ (§4.3), and `fm-00` is **not deleted** right after them:
the retrieval arms (§5.2) run on that very stack and corpus. It is deleted at the end of
§6 step 5, after the retrieval arms and before the first ingest arm, because every stack
binds the same host ports.

`archive_run.sh` refuses when the artifact's recorded running configuration disagrees with
the arm YAML on any factor key (the artifact, not the operator's label, proves which arm
ran), when the corpus changed between the run's endpoints (`corpus_fingerprint_before`,
`corpus_fingerprint`, `corpus_unchanged_at_endpoints` — questions scored across two corpora
are not one observation), when `config_version.divergence_from_selected_file` is non-empty
(the run did not use the settings you selected; Procedure E), when the artifact or the
(arm, stack, run) identity is already in the ledger, when a run other than 1 arrives before
the stack has a pin, or when the artifact predates the run's `ragas-start` entry (a re-run
that wrote nothing must not re-archive run 1 as run 2). Every wrapper also refuses an arm
label that does not match the YAML's own `name: fm-<arm>`, and a checkout whose HEAD is
not the locked campaign code or that carries uncommitted source changes. It records:
artifact path, `corpus_fingerprint`, `corpus_snapshot_id`, `config_version.digest`,
`metadata.code_version.digest`, `ingest_wall_seconds`, the `documents` and
`document_chunks` counts (queried from the stack), and the scored counts per metric.

### 5.2 Retrieval arm (01, 05a, 05b) — on the running `fm-00` stack, no re-ingest

```bash
scripts/benchmarking/feature_matrix/reseed_arm.sh 01 config/benchmarking/feature_matrix/01-rerank-off.yaml
#  1. asserts the corpus fingerprint equals the baseline pin recorded by archive_run.sh (else refuses)
#  2. renders the arm's data_manager block into ~/.archi/archi-fm-00/configs/config.yaml (one key)
#  3. docker compose up --force-recreate config-seed   (upserts static_config; the agent reads it at boot)
#  4. docker compose up --no-deps -d benchmark          (run 1)
scripts/benchmarking/feature_matrix/archive_run.sh 01 1 config/benchmarking/feature_matrix/01-rerank-off.yaml --stack fm-00
scripts/benchmarking/feature_matrix/run_arm.sh 01 --rerun --stack fm-00    # run 2, still on fm-00 (the wrapper would otherwise look for fm-01)
scripts/benchmarking/feature_matrix/archive_run.sh 01 2 config/benchmarking/feature_matrix/01-rerank-off.yaml --stack fm-00
scripts/benchmarking/feature_matrix/qa_arm.sh 01 config/benchmarking/feature_matrix/01-rerank-off.yaml --stack fm-00
scripts/benchmarking/feature_matrix/reseed_arm.sh 00 config/benchmarking/feature_matrix/00-baseline.yaml --no-run   # restore the config; --no-run starts no benchmark
```

The mechanism is `~/.archi/bench-205/swap_arm.sh` generalized from "swap the code" to
"swap one config key": Postgres and the data-manager are never restarted (a restart
re-ingests), the fingerprint is checked before and after, and the benchmark image is not
rebuilt.

### 5.3 QA evaluator run (every arm, after the RAGAS runs, never concurrent with them)

```bash
scripts/benchmarking/feature_matrix/qa_arm.sh $ARM $YAML [--stack fm-00]
```

What it does, and why each step exists:

0. Proves the stack is on the requested arm: the rendered config's chunking, processing,
   stemming and `hierarchical_rerank` keys must equal the arm YAML's, else it refuses. A
   retrieval arm left on `fm-00` after a restore, or a wrong `--stack`, would otherwise
   produce a plausible QA record for the wrong configuration. The ledger entry records
   the rendered config's sha256 and the corpus fingerprint so the record can be tied back.
1. Writes `bench_out/feature_matrix/qa/$ARM.agent-config.yaml` from the stack's rendered
   `~/.archi/archi-<stack>/configs/config.yaml` with **three fields overwritten**:
   `services.chat_app.agent_class`, `default_provider`, `default_model` ←
   `services.benchmarking.agent_class`, `provider`, `model`. The rendered `chat_app` block
   of an evaluate stack names the template defaults (`CMSCompOpsAgent`, `local`,
   `llama3.2`); the QA CLI reads `chat_app`, so without this step it would score the wrong
   agent against a nonexistent Ollama. The file carries no secret (`api_key: EMPTY` is a
   literal).
2. Runs, with `PG_PASSWORD_FILE=$STACK/secrets/pg_password.txt`,
   `HUIT_API_KEY_FILE=$STACK/secrets/huit_api_key.txt`, `OPENAI_API_KEY=EMPTY`, `HOST_MODE=1`:

   ```bash
   archi eval qa \
     --dataset bench_out/feature_matrix/qa/fasrc_ragas_queries.qa-v2.json \
     --agent-config bench_out/feature_matrix/qa/$ARM.agent-config.yaml \
     --agent-spec config/agents/claw/fasrc-docs.md \
     --evaluator-profile config/benchmarking/feature_matrix/qa/evaluator-profile.huit.yaml \
     --output-dir bench_out/feature_matrix/qa/fm-$ARM-r1 \
     --attempts 1 --run-workers 1 --score-workers 4
   ```

   `--run-workers 1` matches the harness's sequential agent calls, so `duration_ms` and
   `time_elapsed` are comparable. `--run` defaults to the next unused number for the stack
   and arm, so the closing baseline's QA run lands as `fm-00-arm00-r2` beside the opening
   `r1`. The dataset is the bank + anchors converted once per
   campaign with `ragas_bank_to_qa_dataset.py` ([#418](https://github.com/fasrc/archi/issues/418));
   its item ids are content-derived, and `compare_runs.py` joins them to the RAGAS rows.
3. Optional, for the console's trend graphs: copy the run directory under claw's
   `~/.archi/archi-claw/data/evaluations/runs/` (root-owned; `sudo`). The console lists
   every run directory it finds, so all arms appear side by side in its history view.

## 6. Order

1. **Phase 0** ([§12](#12-prerequisites-phase-0)) complete: five PRs on `dev`, disk
   reclaimed, config checkout on the new pin, `/home/a2rchi/archi-openai-compat` pulled to the campaign SHA.
2. **Smoke**: every wrapper requires a lock, so lock the smoke inputs first —
   `lock_campaign.sh <smoke copy of 00-baseline.yaml pointing at a 3-question bank> --qa-dataset <its converted bank>` —
   then run `fm-smoke` through every wrapper, `compare_runs.py` on its artifact against
   itself, and `archi delete --name fm-smoke --rmi --rmv`. The smoke rows in the ledger
   carry the smoke lock's hash and are separated from the campaign by the re-lock below.
3. **Lock**: `lock_campaign.sh 00-baseline.yaml --arms-dir config/benchmarking/feature_matrix
   --qa-dataset <converted bank> --relock` hashes every pinned input (bank, anchors, prompt,
   sources, QA dataset and profile), the SUT and judge settings (agent class, model, base
   URL, sampling kwargs, context window, judge model and timeout, metrics), every
   `data_manager` setting that is not an arm factor (chunk sizes, reranker model, hybrid
   weights, categorization provider and categories, scrape limits), the sha256 of every arm
   YAML keyed by its label (so each arm's treatment value is pinned), and the runtime code
   — the git ids of `src/`, `scripts/`, `deploy/`, `pyproject.toml` and `requirements/` —
   into `bench_out/feature_matrix/campaign.lock`. From then on every wrapper refuses an arm
   YAML, dataset, profile or spec whose content differs from the lock, a checkout whose
   runtime trees differ, an artifact whose run started under an earlier lock, and a stack
   deployed under an earlier lock (`run_arm.sh` stamps the lock into the deployment
   directory; a re-lock means redeploying every stack). Then commit the pre-registration
   with the hashes in
   [§2](#2-fixed-factors), the lock's sha256, and the campaign SHA (G1). That docs-only
   commit moves `HEAD` but not the locked trees, so it does not invalidate the lock.
   Nothing below runs before this commit exists.
4. **Opening baseline** `fm-00`: ingest; 3 RAGAS runs; 1 QA run. Its `corpus_fingerprint`
   becomes the pin for §5.2. σ for this corpus comes from the three runs.
5. **Retrieval arms** on `fm-00`: 01, 05a, 05b (2 RAGAS + 1 QA each); restore 00 after
   (`reseed_arm.sh 00 … --no-run`), then **delete `fm-00`** (`archi delete --name fm-00
   --rmi --rmv`): every stack binds the same host ports (5434 / 7882), so the first ingest
   stack cannot deploy while the baseline is up.
6. **Ingest arms**, one stack at a time: 02, 03, 04, 06, 07 (fresh stack; 2 RAGAS + 1 QA;
   delete).
7. **Closing baseline**: a fresh `fm-00` ingest (the stack was deleted at the end of step 5);
   1 RAGAS + 1 QA. `run_arm.sh` prints the next archive number for the reused label
   (run 4), and `qa_arm.sh` takes the next QA number (r2). Its archive is the one
   place the corpus pin may move: `archive_run.sh 00 <run> 00-baseline.yaml --new-corpus`
   is honoured only for arm 00 and only when the stack's latest `ragas-start` was a fresh
   deploy, and the row records the old pin beside the new one. Compare with the opening
   baseline: a different `corpus_fingerprint` means the sources changed under the campaign
   (record which `documents` rows differ); a bank blob hash that changed voids the
   campaign's comparisons from that point.
8. **Compare** every arm to the opening baseline with `compare_runs.py`
   (`--noise-runs` = the three opening runs); write the verdicts into [§9](#9-results-ledger)
   and the pre-registration's outcome section; file follow-up issues per §4.4 step 5.

Arms run strictly serially. The vLLM endpoint is shared with production users; latency
numbers are comparable only when taken in the same hours, so RAGAS runs start at the same
time of day across arms where possible, and the ledger records start and end timestamps.

## 7. Invariants the compare step enforces

`compare_runs.py` refuses (exit 2) or stops (exit 3) rather than printing a number when:

| Invariant | Gate | Check |
|---|---|---|
| Same questions in both arms | G4 | question text sets equal; no override |
| Same corpus for a retrieval arm | G3 | `corpus_fingerprint` equal to the baseline pin. The wrappers compute the live fingerprint with the harness's own `CORPUS_STATE_QUERY` and `corpus_fingerprint` routine (documents, chunks, parent nodes; sha256), run inside the stack's data-manager container, so the pin taken from an artifact and the live check are one digest |
| Different corpus for an ingest arm is declared | G3 | `--corpus-differs-by-design`, with both fingerprints and the document/chunk counts printed |
| The run used the selected settings | Procedure E | `divergence_from_selected_file` empty on every artifact |
| Same code | Procedure E | `metadata.code_version.digest` equal across the campaign |
| Paired on rows scored in both arms | G5, G6 | join on question text; `status == ok` and finite in both. Question text is the key because the harness dedupes the bank and the anchors on exact text before asking (105 + 5 − 1 = 109 unique rows; the bank itself has 105 unique texts). The tool refuses an artifact that carries the same text twice. |
| The shared should_refuse row is an anchor | Gap 3 | one anchor duplicates a bank row; the harness keeps the bank row's reference. That row is treated as the anchor: it appears in the anchor block and is excluded from the bank aggregate, which therefore covers 104 rows |
| Honest denominators | #279 | scored counts recomputed from finite values, printed per metric per arm; arms whose ok-row counts differ by more than 5 are flagged |
| Anchors are tripwires, not bank rows | Gap 3 | the 5 anchors identified by question text, reported in their own block, excluded from bank aggregates |
| A verdict needs a noise floor | G2, G7 | SIGNIFICANT only with σ known and \|Δ\| > max(2·SE, 2·σ) |
| The stack belongs to the active lock | lock | `run_arm.sh` stamps the lock's sha256 into the deployment directory at deploy; re-run, re-seed, QA and archive refuse a stack stamped with another lock |
| The arm is the pre-registered treatment | lock | the arm YAML's sha256 equals the lock's per-arm manifest entry for its label |
| A run is tied to a start | provenance | archive refuses when the ledger holds no `ragas-start` row for the stack; the live document/chunk counts must be readable (the stack is deleted right after) |
| The QA corpus held for the whole run | G3 | `qa_arm.sh` re-reads the fingerprint after `archi eval qa` returns and writes no row if it moved |

## 8. Budget

Measured on this host: a 109-question RAGAS run ≈ 1.25 h (the #305 bracket: 2.5 h for two
identical arms); an ingest 0.5–2 h (21 min scrape + 7 min embed on 2026-08-11 with
categorization on; 2 h 2 min under load on 2026-08-27); a QA run ≈ 1–1.5 h (109 agent
calls plus atom extraction and judging).

| Step | Stacks | Ingests | RAGAS runs | QA runs | Hours (approx.) |
|---|---|---|---|---|---|
| Opening baseline | 1 | 1 | 3 | 1 | 7 |
| Retrieval arms 01, 05a, 05b | 0 (reuse) | 0 | 6 | 3 | 11 |
| Ingest arms 02, 03, 04, 06, 07 | 5 | 5 | 10 | 5 | 29 |
| Closing baseline | 1 | 1 | 1 | 1 | 4.5 |
| **Total** | **7** | **7** | **20** | **10** | **≈ 52** |

Disk: one stack ≈ 10 GB of images (two 4.8 GB service images + Postgres) plus small
volumes; `archi delete --rmi --rmv` after each ingest arm keeps one stack on disk at a
time. Judge cost: ≈ 20 runs × 109 questions × 5 RAGAS metrics, plus 10 QA runs × 109
questions × (extraction + judgment) on HUIT Bedrock.

## 9. Results ledger

Machine record: `bench_out/feature_matrix/ledger.json`, one entry per run, appended by
`archive_run.sh` and `qa_arm.sh`:

```json
{"arm": "03", "run": 1, "kind": "ragas", "stack": "fm-03", "started": "…", "finished": "…",
 "artifact": "bench_out/feature_matrix/benchmarking-fm-03-<ts>.json",
 "corpus_fingerprint": "…", "corpus_snapshot_id": "…", "config_digest": "sha256:…",
 "code_digest": "sha256:…", "ingest_wall_seconds": 4321.0, "documents": 1132, "chunks": 6096,
 "scored": {"answer_relevancy": "109 of 109", "…": "…"}, "degraded": 0}
```

Human record (filled as arms complete; one row per arm, baseline first):

| Arm | Runs | Fingerprint | Ingest (s) | Chunks | Primary Δ (MDE) | Verdict | Cost note | Artifacts |
|---|---|---|---|---|---|---|---|---|
| 00 | 3 RAGAS + 1 QA | `sha256:fc8ee1b5…` | 4959.9 | 6926 (1091 docs) | reference | — | ingest 82.7 min on a quiet host | `benchmarking-fm-00-20260905_{001802,024701,053600}.json`; QA `qa/fm-00-arm00-r1` |
| 01 | | | | | | | | |
| 02 | | | | | | | | |
| 03 | | | | | | | | |
| 04 | | | | | | | | |
| 05a | | | | | | | | |
| 05b | | | | | | | | |
| 06 | | | | | | | | |
| 07 | | | | | | | | |
| 00 (closing) | | | | | | drift check | | |

## 10. Risks

- **VPN drop or vLLM outage mid-run** → degraded or failed rows. The denominator rule
  flags the arm; rerun the RAGAS run rather than pairing a short arm.
- **Disk.** 13 GB were free on 2026-09-03 against ≈ 10 GB per stack. Phase 0 reclaims
  Docker build cache (≈ 38 GB) and dangling images (≈ 14 GB); the runbook deletes each
  ingest stack before the next.
- **Port collision.** The `archi-ragas-205` stack holds 5433/7881; the campaign uses
  5434/7882, so it can stay until the operator decides otherwise.
- **Co-location with production (host deviation).** The arms share this box with the
  production `dev` stack and both vLLM servers. Ingest and embedding are CPU-side and
  the embedder is pinned `device: cpu`, so the GPUs stay with vLLM — but Time to Result
  and Time to Ingest carry whatever else the host is doing. Latency is comparable
  across arms only because every arm pays the same tax; it is not comparable with claw
  numbers or with the 2026-08 runs. Artifacts do not record the host ([#433](https://github.com/fasrc/archi/issues/433)),
  so the ledger records it by hand.
- **Sources change under the campaign.** The closing baseline detects it via
  `corpus_fingerprint`; the pre-registration records the bank blob hash so bank edits are
  detectable too. All 105 bank rows are `draft` (no `source_hashes`), so the drift
  tripwire ([#213](https://github.com/fasrc/archi/issues/213)) cannot help here yet.
- **Parent-row accumulation** ([#411](https://github.com/fasrc/archi/issues/411)) does
  not apply: every ingest arm is a fresh stack with one ingest.
- **The deploy clone is the code.** `~/miniforge3/envs/archi` is an editable install of
  `/home/a2rchi/archi-openai-compat`; `archi evaluate` builds images from that tree. It must sit on the
  campaign SHA for the whole campaign, and `git_info`/`code_version.digest` prove it per
  artifact.
- **Draft references.** The QA atoms derive from `reference` fields that no human has
  locked. Atom verdicts are therefore relative (arm vs. baseline on the same atoms), not
  absolute quality.
- **The `easy_retrieve` alarm is a prompt to look, not a verdict.** Its threshold (a drop
  larger than σ) applies a run-mean σ to a single question, whose score is far noisier
  than a mean: `compare_runs.py` on two same-code 2026-08 runs raised it three times. The
  tool prints the delta and the threshold beside every alarm; an alarm on its own never
  changes a verdict, and a repeated alarm on one anchor across arms is the signal worth
  chasing. Tightening the rule (a per-question threshold) is a change to this plan and to
  the pre-registration, to be made before the campaign starts or not at all.

## 11. Out of scope

`chunking.chunk_overlap` (#403), embedding models (#216), interaction effects between
toggles, prompt variants (the prompt-sweep harness exists for that), the unattended serial
driver (#396 Gap 2 — the runbook and wrappers replace it for this campaign; a driver issue
is filed if a second campaign is scheduled), and migrating the ten pre-#279 artifacts in
`bench_out/`.

## 12. Prerequisites (Phase 0)

No arm runs before all of these are on `dev` and done:

| # | Item | Why the campaign is wrong without it |
|---|---|---|
| 1 | [#279](https://github.com/fasrc/archi/issues/279) — `null` not `NaN` in artifacts; scored count = finite values | a "109 of 109" that has 108 finite values biases every mean and the denominator rule |
| 2 | [#378](https://github.com/fasrc/archi/issues/378) — stall budget + absolute ceiling for the ingest wait | the 2026-08-27 ingest was killed healthy at exactly 7200 s, 2 min before it finished |
| 3 | [#417](https://github.com/fasrc/archi/issues/417) — record `ingest_wall_seconds` per arm | the time-to-ingest family has no source otherwise |
| 4 | [#418](https://github.com/fasrc/archi/issues/418) — RAGAS bank → qa-dataset-v2 converter | the QA CLI refuses `user_input` rows; the atoms family cannot run on the bank |
| 5 | [#419](https://github.com/fasrc/archi/issues/419) — `compare_runs.py` | every verdict in §4.4 is computed by it; a notebook snippet cannot enforce §7 |
| 6 | Arm YAML files merged in archi-config and the deploy pin bumped (`deploy-pin-2026-09a`) | the on-pin checkout must carry the files `archi evaluate` reads |
| 7 | Operator: reclaim disk; converge the config checkout to the required pin; pull `/home/a2rchi/archi-openai-compat` to the campaign SHA | see §10 |

## 13. Operating log — what works, what does not

Kept current as the campaign runs. The point of this section is reproducibility: someone
re-running this campaign cold should hit none of the walls below twice. Every entry was
observed on **holygpu7c0717** on the date given.

### 13.1 Preconditions that must actually hold

Checked before the 2026-09-04 lock; a failure in any of these is silent or misleading
rather than loud, which is why they are listed.

| # | Precondition | How to check | Why it bites |
|---|---|---|---|
| 1 | `config/` at the campaign pin | `git -C config rev-parse HEAD` = the `CONFIG_SHA` in `deploy/scripts/lib.sh` | `redeploy.sh` refuses an off-pin checkout with local edits — correctly, and it refuses *before* touching containers |
| 2 | Checkout on the campaign code, runtime trees clean | `git status --porcelain --untracked-files=no -- src scripts deploy` empty | `lock_campaign.sh` and every wrapper refuse otherwise. Untracked files elsewhere (docs) are fine by design |
| 3 | `RAGAS_ENV_FILE` exported | `[ -f "$RAGAS_ENV_FILE" ]` | `run_arm.sh` dies without it. It does **not** persist between shells — export it in every invocation |
| 4 | Host conda env can build embeddings | `python -c "from langchain_huggingface import HuggingFaceEmbeddings"` | **`archi eval qa` runs in-process on the host**, not in a container. Drift here silently voids every QA run (§13.2 #5) |
| 5 | ghcr.io login + `ijson` present | `grep ghcr.io ~/.docker/config.json`; `python -c "import ijson"` | Both fail the deploy before containers move |
| 6 | Ports 5434 / 7882 free | `ss -ltn \| grep -E ':(5434\|7882)'` | Every stack binds the same pair, so only one arm can exist at a time |
| 7 | Disk headroom | `df -h /scratch` (Docker root here) — ~10 GB per stack | The plan's "13 GB free" figure was claw's, not this host's |

### 13.2 Defects found, and what to do about them

| # | Symptom | Cause | Resolution | Status |
|---|---|---|---|---|
| 1 | §6 step 2 says run `fm-smoke` through every wrapper, but the wrappers reject the label | `fm_require_arm` is `^[0-9]{2}[a-z]?$` and `lock_campaign.sh` hardcodes `fm_require_arm_yaml 00` | Use `run_arm.sh 00 <yaml> --stack fm-smoke`; `--stack` is honoured on the deploy path too. Verified that `archi evaluate --name` drives the deployment dir and every `container_name`, while the YAML's own `name:` does not (live proof: `dev.yaml` says `name: archi_dev`, containers are `chatbot-dev`) | **works, doc corrected** |
| 2 | §5 pointed at `~/Projects/archi` and `/home/austin/.archi/archi-ragas-205/.env` | Written on claw | Paths corrected to this checkout and `~/.archi/.env.benchmark` | **fixed** |
| 3 | Pre-reg claimed the campaign prompt was byte-identical to the GPU host's production spec; that file did not exist | The host served `fasrc-v2.md` (name `FASRC`, `search_vectorstore_hybrid` only) | Production migrated to `config/agents/claw/fasrc-docs.md`; `fasrc-v2.md` retired to `agents/archive/`. Claim is now true rather than asserted | **fixed, verified live** |
| 4 | Arms would run with **no in-loop context bound** while production has 32768 | Every arm YAML omitted `services.chat_app.context_editing`; `base-config.yaml` emits the block only when declared | Added to all nine arms, matching production byte for byte. The lock's `sut.context_editing` is the check — it read `null` before | **fixed pre-lock** |
| 5 | QA run "completes", exits 0, and scores **nothing** | `archi eval qa` runs in-process on the host; the host env had `transformers` 5.9.0 against `huggingface_hub` 0.36.2 → `ImportError: cannot import name 'is_offline_mode'`, surfaced as the misleading `Could not import sentence_transformers` | `pip install "transformers==4.57.6" "sentence-transformers==5.1.2"` (the versions the benchmark image runs; the latter is also the repo pin). Re-run went from `execution_failed: 8` to `scored: 8` | **fixed** |
| 6 | A QA run that scored nothing still gets a ledger row and exit 0 | `qa_arm.sh` does not fail closed on `scored: 0`, unlike `archive_run.sh` on the RAGAS side | **Open.** Until fixed, read `summary.json` → `attempt_lifecycle_counts.scored` after every QA run. A zero there with exit 0 is the silent-failure shape. `bench_out/feature_matrix/check_qa_scored.sh` does this across every QA run dir. **Cannot be fixed mid-campaign**: `scripts/` is inside the locked code trees, so editing `qa_arm.sh` forces a `--relock` and a redeploy of every stack | **open — [#434](https://github.com/fasrc/archi/issues/434)** |
| 7 | `archi delete --rmv` aborts non-interactively | It calls `click.confirm(..., abort=True)` and there is no `--force`/`--yes` | `printf 'y\n' \| archi delete --name <stack> --rmi --rmv` | **works** |
| 8 | A retired agent spec survives a redeploy in the staged agents dir | `archi create --force` cannot remove `data/evaluations` (root-owned), so it abandons removal of the whole `data/` dir — the staged `data/agents/` keeps old files | Delete the retired spec from `~/.archi/archi-<name>/data/agents/` by hand after the redeploy, then restart the chatbot | **works, manual step** |
| 10 | Arm 00's ingest ran while the host was at load ~44 | **A `redeploy.sh` of production `dev` triggers a full re-ingest** (1145 files, ~30 min at ~26 cores). The agent migration on 2026-09-04 was redeployed minutes before arm 00 started, so the baseline ingest overlapped it | **Sequence any production deploy well BEFORE an arm, never during.** Arm 00 was torn down 7 min in and restarted once `data-manager-dev` went quiet (`bench_out/feature_matrix/fm00_start_when_quiet.sh` waits for 3 consecutive sub-100 % CPU reads). This matters because arm 00's `ingest_wall_seconds` is the reference all five ingest-side arms are compared against — a one-off production re-ingest is a tax only arm 00 would pay, breaking §10's "every arm pays the same tax" premise | **fixed by restart** |
| 11 | A hung run would block the chain forever | `archive_run.sh --wait` is an unbounded poll — `while [ running ]; do sleep 30; done` — with no timeout and no stall detection (unlike the ingest wait, which got a stall budget in #378) | **Open.** There is no dead-man's switch: check that `bench_out/feature_matrix/fm00_baseline.log` has advanced. Budget ≈ 3 h for run 1 (ingest + 109 questions + judging) and ≈ 1.5 h per re-run | **open — operational check** |
| 12 | Idle stacks still hold memory on the measuring host | `data-manager-ragas-0827` / `postgres-ragas-0827` have been up 7 days (~1.6 GB, ~0 % CPU). They publish no ports so they cannot collide | Operator's call before arm 01: take them down, or record them in the ledger as a constant part of the host tax | **open — operator decision** |
| 9 | Commits intermittently blocked by a red gate | `tests/unit/evaluation/qa/test_jobs_history.py::test_job_manager_terminates_running_evaluation_process` is flaky on `dev` — 3 failures in 5 consecutive runs, and it passes on retry with no code change. Its own race: it waits for status `running`, then reads `manager._processes[job_id]`, which the reaper may already have removed | Retry the commit. **Never** `--no-verify`. Safe to fix during a campaign — `tests/` is NOT in the locked trees | **open — [#435](https://github.com/fasrc/archi/issues/435)** |

### 13.3 Verified working (2026-09-04 smoke, `fm-smoke`, 3 URLs / 8 questions)

The whole wrapper chain was exercised end to end before the campaign locked:

- `lock_campaign.sh` — pins bank, anchors, prompt, sources, QA dataset and profile, SUT and
  judge settings, every non-factor `data_manager` key, all nine arm sha256s, and the runtime
  trees. Re-lock with `--relock`.
- `run_arm.sh` → `archive_run.sh --wait` — artifact archived with
  `divergence_from_selected_file: []`, corpus fingerprint pinned, `ingest_wall_seconds`
  recorded (**Gap 1 closed**, 45.27 s on the smoke corpus), and scored counts recomputed
  from finite values (**#279 working**: honest `7 of 8`, `degraded 1`).
- `qa_arm.sh` — proves the stack is on the arm, rewrites the `chat_app` SUT fields from
  `services.benchmarking`, and records the rendered config sha256 and fingerprint.
- `compare_runs.py` — self-comparison gave `+0.0000` on every metric, evaluated G3/G4/
  Procedure E as pass, raised an honest G8 alarm on an unscored anchor, excluded anchors
  from the bank aggregate, and **refused to call anything SIGNIFICANT with no measured
  noise floor** (G2).
- A docs-only commit moves `HEAD` but not the locked trees — confirmed: `HEAD:src` equals
  `3170498c:src`, and `run_arm.sh` passed `fm_require_code_lock` afterwards.

### 13.4 The sequence that actually ran

```bash
export RAGAS_ENV_FILE=/home/a2rchi/.archi/.env.benchmark   # every shell
W=scripts/benchmarking/feature_matrix

# once per campaign: the QA dataset (105 bank + 5 anchors - 1 duplicate = 109 items)
python scripts/benchmarking/ragas_bank_to_qa_dataset.py \
    config/benchmarking/fasrc_ragas_queries.json \
    --anchors examples/benchmarking/anchor_questions.json \
    --out bench_out/feature_matrix/qa/fasrc_ragas_queries.qa-v2.json

# smoke: a copy of 00-baseline.yaml (name STAYS fm-00) pointed at a 3-URL sources list and
# a 3-row bank, in its own arms dir, then every wrapper against --stack fm-smoke
$W/lock_campaign.sh bench_out/feature_matrix/smoke/00-baseline.yaml \
    --arms-dir bench_out/feature_matrix/smoke \
    --qa-dataset bench_out/feature_matrix/smoke/bank.smoke.qa-v2.json
$W/run_arm.sh 00 bench_out/feature_matrix/smoke/00-baseline.yaml --stack fm-smoke
$W/archive_run.sh 00 1 bench_out/feature_matrix/smoke/00-baseline.yaml --stack fm-smoke --wait
$W/qa_arm.sh 00 bench_out/feature_matrix/smoke/00-baseline.yaml --stack fm-smoke \
    --dataset bench_out/feature_matrix/smoke/bank.smoke.qa-v2.json
python scripts/benchmarking/compare_runs.py <artifact> <artifact>
printf 'y\n' | archi delete --name fm-smoke --rmi --rmv

# campaign: re-lock on the real inputs, commit the pre-registration (G1), then arm 00
$W/lock_campaign.sh config/benchmarking/feature_matrix/00-baseline.yaml \
    --arms-dir config/benchmarking/feature_matrix \
    --qa-dataset bench_out/feature_matrix/qa/fasrc_ragas_queries.qa-v2.json --relock
git commit docs/eval/preregs/2026-09-feature-matrix.md     # G1; docs-only, lock survives
$W/run_arm.sh 00 config/benchmarking/feature_matrix/00-baseline.yaml
```

Because the smoke stack is `fm-smoke` and not `fm-00`, its ledger rows and corpus pin sit
under their own stack key: the campaign's `fm-00` still numbers its runs from 1 and pins its
own corpus. Using `fm-00` for the smoke would have forced runs 2-4 and a stale pin.

### 13.5 Campaign run log

| When (EDT) | Event |
|---|---|
| 2026-09-04 15:39 | Campaign locked (`3e07ae79…`) on the corrected arms; pre-registration committed (`c2deac32`) — gate G1 satisfied |
| 2026-09-04 15:46 | Arm 00 started — **aborted 7 min in**: a production re-ingest triggered by that afternoon's dev redeploy was running concurrently (§13.2 #10). Stack deleted with its volumes; ledger keeps the orphan `ragas-start` row for arm 00 / `fm-00`, which is expected and harmless (`fm_next_run` counts archived `ragas` rows, not starts, so run numbering still begins at 1) |
| 2026-09-05 03:07 | **Opening baseline complete.** 3 RAGAS runs + 1 QA run archived, `degraded = 0` on all three, QA `scored: 109/109`. Ingest 4959.9 s (82.7 min), 1091 docs / 6926 chunks, one corpus fingerprint across all three runs |
| 2026-09-05 12:00 | **σ checkpoint PASSED — N = 2 stands, no re-lock needed.** Measured σ (paired, 104 questions, by `compare_runs.py --noise-runs`): `context_precision` **0.0040**, `context_recall` **0.0109**, `answer_relevancy` **0.0161**, `faithfulness` **0.0209**. Every value is *below* the planning prior (0.016 / 0.021 / 0.025 / 0.027), so the pre-registered escalation rule — σ on the primary metric more than 50 % above prior — did not trigger. MDE on `context_precision` ≈ 0.016 (2·SE dominates 2·σ). All three replicates were mutually "not distinguishable", which is the result you want from replicates |
| 2026-09-04 16:13 | Watcher armed: waits for `data-manager-dev` to go quiet, then runs the opening baseline (3 RAGAS runs + 1 QA) and **stops before arm 01** for the pre-registered σ checkpoint |

## Appendix A — how to re-derive the numbers

All from `~/Projects/archi-ragas-merge` (artifacts in `bench_out/` contain bare `NaN`;
the snippets tolerate it).

σ of run means (§4.1):

```bash
python3 - <<'EOF'
import json,glob,statistics as st
M=["answer_relevancy","faithfulness","context_precision","context_recall"]
vals={m:[] for m in M}
for p in sorted(glob.glob("bench_out/benchmarking-ragas-205-2026081*.json")):
    d=json.loads(open(p).read().replace("NaN","null"))
    for arm in d["benchmarking_results"]:
        for m in M: vals[m].append(arm["total_results"][f"aggregate_{m}"])
for m in M: print(f"{m:18s} n={len(vals[m])} mean={st.mean(vals[m]):.4f} sd={st.stdev(vals[m]):.4f}")
EOF
```

Paired per-question SE (§4.2):

```bash
python3 - <<'EOF'
import json,glob,math,statistics as st
M=["answer_relevancy","faithfulness","context_precision","context_recall"]
def load(p):
    d=json.loads(open(p).read().replace("NaN","null"))
    return {r["question"]:r for r in d["benchmarking_results"][0]["single_question_results"].values() if r.get("status","ok")=="ok"}
runs=[load(p) for p in sorted(glob.glob("bench_out/benchmarking-ragas-205-20260817_*.json"))]
real=lambda x: isinstance(x,(int,float)) and not math.isnan(x)
for i,j in ((0,1),(1,2),(0,2)):
    a,b=runs[i],runs[j]; common=a.keys()&b.keys()
    for m in M:
        d=[b[q][m]-a[q][m] for q in common if real(a[q].get(m)) and real(b[q].get(m))]
        sd=st.stdev(d); print(f"r{i+1}-r{j+1} {m:18s} n={len(d)} sd_delta={sd:.3f} 2SE={2*sd/math.sqrt(len(d)):.3f}")
EOF
```

Pins (§2), from `/home/a2rchi/archi-openai-compat`:

```bash
git -C config hash-object benchmarking/fasrc_ragas_queries.json
sha256sum config/benchmarking/fasrc_ragas_queries.json config/agents/claw/fasrc-docs.md config/lists/sources.list examples/benchmarking/anchor_questions.json
cmp deploy/fasrc-dev/agents/fasrc-docs.md config/agents/claw/fasrc-docs.md && echo identical
```

Anchor dedupe (§2): `python3 -c "import json;b={r['user_input'].strip() for r in json.load(open('config/benchmarking/fasrc_ragas_queries.json'))};print(sum(a['user_input'].strip() in b for a in json.load(open('examples/benchmarking/anchor_questions.json'))))"` → `1`.

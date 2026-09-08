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

| Arm | FUT — feature under test (`key: baseline → arm`) | Runs | Fingerprint | Ingest (s) | Chunks | Primary Δ (MDE) | Verdict | Cost note | Artifacts |
|---|---|---|---|---|---|---|---|---|---|
| 00 | — *(reference)* | 3 RAGAS + **3 QA** | `sha256:fc8ee1b5…` | 4959.9 | 6926 (1091 docs) | reference | — | ingest 82.7 min on a quiet host. **Gold-atom noise floor (QA r1/r2/r3):** pass rate 0.440 / 0.398 / 0.454 (σ 0.024, s 0.029); atom score 0.499 / 0.454 / 0.483 (σ 0.018, s 0.023); required-atom recall 0.630 / 0.604 / 0.611 (σ 0.011, s 0.014); atoms contradicted 47 / 70 / 52 on identical setups | `benchmarking-fm-00-20260905_{001802,024701,053600}.json`; QA `qa/fm-00-arm00-r{1,2,3}` |
| 01 | `retrievers.hierarchical_rerank.enabled: true → false` | 2 RAGAS + 1 QA | `sha256:fc8ee1b5…` (= 00) | n/a (re-seed) | 6926 | `context_precision` −0.0253 / −0.0095 (MDE 0.063) | **no measurable difference on the primary metric** — the ADR 0003 "+19 % RAGAS" claim is NOT reproduced. Trade-off: `context_recall` −0.072 / −0.052, consistent in both runs and opposite in sign to the baseline's own replicate spread (+0.021 / +0.016) | **latency ~halved**: 48.2 s → 24.2 / 28.8 s mean, p90 100.9 → 36.8 / 58.3 s. Gold atoms higher — **outside 2σ of the three-run baseline floor on all three metrics**: pass rate 0.523 vs 0.431 ± 0.024 (+0.092, 2σ 0.047), atom score 0.591 vs 0.479 ± 0.018 (+0.112, 2σ 0.037), required-atom recall 0.737 vs 0.615 ± 0.011 (+0.122, 2σ 0.023). One arm-side QA run, so this is one draw 3–5σ from the floor, not a paired test | `benchmarking-fm-00-20260906_{054238,073529}.json`; QA `qa/fm-00-arm01-r1`; report `reports/arm-01.md` |
| 02 | `chunking.strategy: sentence → character` **+** `retrievers.hierarchical_rerank.enabled: true → false` (two keys, pre-registered: character chunks carry no `parent_id`) | 2 RAGAS + 1 QA | `sha256:d7417a68…` (own corpus, ingest arm) | 4863.5 | 7177 (1091 docs; +3.6 % vs 6926) | `context_precision` −0.0698 / −0.0730 (MDE 0.0806 / 0.0820) | **held constant on the primary metric, but only just** — both deltas sit within 0.01 of a wide MDE (per-question deltas vary a lot when chunking changes). `context_recall` **−0.183 / −0.218 REGRESSED** (MDE 0.069 / 0.073) — the largest regression in the campaign; arm 01 isolated the rerank-off half at −0.072 / −0.052, so character chunking on its own costs roughly another −0.13 recall. Source accuracy 0.877 → **0.840 / 0.840** (arm 01: unchanged), so the chunking half also costs ~4 points of citation accuracy. `faithfulness` / `answer_relevancy` inside MDE | Latency 48.2 → 28.2 / 25.0 s (≈ arm 01's 24.2 / 28.8: the speed-up is the rerank-off half; chunking strategy is latency-neutral). Blowouts 4 / 3 (baseline 6–11). Ingest 4863.5 s ≈ baseline 4959.9 s. **Gold atoms IMPROVED outside 2σ on all three** — pass rate 0.537 (+0.106, 2σ 0.047), atom score 0.570 (+0.091, 2σ 0.037), required-atom recall 0.715 (+0.100, 2σ 0.023) — and within ~0.02 of arm 01's values, so the atom gain is rerank-off's, not the chunking's. Procedure B: σ is the baseline corpus's, applied as an estimate. QA scored 108 (one non-scored, §13.2 #16) | `benchmarking-fm-02-20260908_{042141,061046}.json`; QA `qa/fm-02-arm02-r1`; report `reports/arm-02.md` |
| 03 | `processing.categorization.enabled: true → false` | 2 RAGAS + 1 QA | `sha256:570ccaa8…` (own corpus, ingest arm) | **3802.3** (−23 % vs 4959.9) | 6896 (1091 docs; 30 fewer than baseline's 6926 with the same sentence chunking — small unexplained delta, watch whether arms 04/06/07 reproduce 6926) | `context_precision` −0.0138 / −0.0232 (MDE 0.0247 / 0.0269) | **held constant on the primary metric; no consistent effect anywhere.** `context_recall` −0.043 REGRESSED (MDE 0.041) / −0.031 held (MDE 0.036) — a borderline dip, one run just outside; `faithfulness` / `answer_relevancy` inside MDE; source accuracy 0.877 → 0.868 / 0.840 (mixed). Gold atoms: pass rate −0.023 held, atom score +0.019 held, required-atom recall **+0.038 IMPROVED** (2σ 0.023) — one of three, positive, so no coherent atom direction. This is the outcome the 2026-09-01 code audit predicted: `metadata.llm_category` is write-only (`processing.py:475`), read by no retriever, prompt or embedding path, so the feature cannot move retrieval — the residual wobble is corpus-to-corpus noise (Procedure B) | **The whole effect is cost: ingest 19 min faster** with no per-document categorization call (~1080 LLM calls saved per ingest). Latency 48.2 → 48.7 / 51.5 s (flat). Blowouts 6 / 5 (baseline 6–11), tool calls per normal question 3.69 / 4.39 (baseline 3.69). QA scored 108 | `benchmarking-fm-03-20260908_{104423,132337}.json`; QA `qa/fm-03-arm03-r1`; report `reports/arm-03.md` |
| 04 | `retrievers.stemming.enabled: false → true` | | | | | | | | |
| 05a | `retrievers.hierarchical_rerank.num_documents_to_retrieve: 5 → 3` | 2 RAGAS + 1 QA | `sha256:fc8ee1b5…` (= 00) | n/a (re-seed) | 6926 | `context_precision` −0.0141 / −0.0054 (MDE 0.0225 / 0.0214) | **held constant on the primary metric, but the arm is worse.** `context_recall` −0.0872 / −0.0611 REGRESSED in both runs, against a baseline replicate spread of the opposite sign (+0.021 / +0.016); source accuracy 0.877 → 0.792 on both runs (106 scored). `faithfulness` −0.0513 / −0.0548 straddles its MDE (inside on run 1, outside on run 2) — reported as the null on run 1, so no faithfulness claim. Judged on `context_precision` alone this arm reads as a wash; it is not | **no latency payoff**: 48.2 s → 55.1 / 45.8 s mean (p90 100.9 → 149.9 / 113.7), i.e. retrieving fewer documents did not buy speed. Gold atoms **inside 2σ of the three-run baseline floor** on all three metrics (atom score 0.482 vs 0.479 ± 0.018; pass rate 0.431 vs 0.431; required-atom recall 0.622 vs 0.615) — no atom effect | `benchmarking-fm-00-20260906_{112444,134950}.json`; QA `qa/fm-00-arm05a-r1`; report `reports/arm-05a.md` |
| 05b | `retrievers.hierarchical_rerank.num_documents_to_retrieve: 5 → 8` | 2 RAGAS + 1 QA | `sha256:fc8ee1b5…` (= 00) | n/a (re-seed) | 6926 | `context_precision` −0.0135 / −0.0128 (MDE 0.0178 / 0.0168) | **held constant on the primary metric; the arm is better on retrieval.** `context_recall` +0.0604 **IMPROVED** (MDE 0.0417) / +0.0276 held (MDE 0.0418) — same sign both runs and same sign as the baseline's own replicate spread (+0.021 / +0.016), so read the second run as a real but sub-MDE lift. **Source accuracy 0.877 → 0.962 / 0.953** — the largest clean effect in the campaign so far, in both runs. `faithfulness` +0.028 / +0.035 inside MDE; `answer_relevancy` flat | **latency flat**: 48.2 s → 47.2 / 47.2 s mean (p90 100.9 → 126.5 / 74.4); per normal question 34.3 → 35.0 / 38.7 s; blowouts 7 / 4 (baseline 6–11). Gold atoms **down** from ONE QA run: passed 48 → 45, atom score 0.499 → 0.457, required-atom recall 0.630 → 0.603 — over **108** scored, one item `preparation_failed` (§13.2 #16); **inside 2σ of the three-run baseline floor** on all three metrics (atom score 0.457 vs 0.479 ± 0.018, Δ −0.022 vs 2σ 0.037; pass rate −0.014 vs 0.047; required-atom recall −0.012 vs 0.023) — the apparent atom drop was noise | `benchmarking-fm-00-20260906_{175600,203046}.json`; QA `qa/fm-00-arm05b-r1`; report `reports/arm-05b.md` |
| 06 | `processing.html_to_markdown.enabled: true → false` | | | | | | | | |
| 07 | `chunking.strategy: sentence → markdown` | | | | | | | | |
| 00 (closing) | — *(reference, drift check)* | | | | | | drift check | | |

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
| 13 | **Every re-seeded retrieval arm fails Procedure E.** `archive_run.sh` refuses with `divergence_from_selected_file = ['data_manager.retrievers.hierarchical_rerank.enabled']`, even though the run used the arm's setting correctly | The benchmark service is the only one of six in `base-compose.yaml` that does NOT bind-mount `./configs`, so it reads a config baked into its image at build time. `reseed_arm.sh` updates the host file and Postgres — the agent obeys Postgres (verified: `hierarchical_rerank disabled: falling back to HybridRetriever`), but the harness compares against the stale baked copy. The baseline never tripped this because baked and Postgres both held baseline values | Added the mount by hand to `~/.archi/archi-fm-00/compose.yaml`. Verified the baked and host configs differ in **exactly one key**, the one under test, so the mount changes nothing else. **The patch is not durable** — `archi create`/`evaluate` re-renders that file — but nothing the retrieval arms do re-renders it, and if it were lost the failure is loud (a Procedure E refusal), never a silent wrong number. Permanent fix: [#439](https://github.com/fasrc/archi/issues/439), blocked until the campaign ends because `src/` is lock-pinned. Ingest arms are unaffected — a fresh `archi evaluate` bakes the right config | **worked around — [#439](https://github.com/fasrc/archi/issues/439)** |
| 14 | **G8 anchor gate fires on runs that changed nothing.** The arm-05a report shows `easy_retrieve ALARM` naming baseline replicates `20260905_024701` and `20260905_053600` — both are runs of the *baseline* config against the baseline reference, so a config delta of zero trips the tripwire | The per-question alarm threshold is the **aggregate** noise floor σ (`scripts/benchmarking/compare_runs.py:1293-1298`, threshold = `sigmas.get(metric)`, i.e. 1σ not 2σ), which is the spread of a *mean over 104 questions*. Per-question spread is much larger. Using the tool's own σ (from `reports/arm-05a.json`, identical to the §13.5 σ checkpoint) against per-question σ measured over the three baseline replicates: `faithfulness` **0.0209 vs 0.1081 (5.2×)**, `answer_relevancy` 0.0111 vs 0.0743 (6.7×), `context_precision` 0.0050 vs 0.0162 (3.2×), `context_recall` 0.0109 vs 0.0160 (1.5×). On the SLURM anchor the three identical-config replicates span `faithfulness` 0.789 / 0.880 / 0.950 — a 0.161 range against a 0.0209 threshold. The gate is structurally guaranteed to alarm on the noisy metrics | **Open — does NOT invalidate any arm.** It fires on the baseline itself, and no arm verdict rests on it; arm verdicts come from the paired MDE tables, where the aggregate σ is the right scale because the compared quantity *is* an aggregate. `scripts/` is lock-pinned → no fix until the campaign closes; fix is to scale the anchor threshold to per-question spread (e.g. 2× per-question σ from the replicates) instead of reusing the aggregate σ. **File after the campaign.** Scope: G8 walks a curated 5-anchor set, 2 alarm; across all 31 rows carrying `anchor_type: easy_retrieve`, baseline-vs-itself would alarm 22/31 on `faithfulness`, 17/31 on `answer_relevancy`, 5/31 on `context_precision`, 0/31 on `context_recall`. **Not every G8 alarm is noise:** arm 01 drove `context_precision` on this anchor to **0.000 in both runs** (baseline 0.29-0.34) — rerank-off zeroing precision on an easy-retrieve question is a genuine signal and belongs in the arm 01 discussion, not in this defect | **open** |
| 15 | `context_recall` on the SLURM GPU-partition anchor is pinned at exactly **0.500** in all seven runs to date — three baseline replicates, both arm 01 runs, both arm 05a runs — regardless of rerank or `k` | Unconfirmed; the shape (an exact one-half, invariant to every retrieval change) is what a two-claim reference answer looks like when only one claim exists in the corpus. The question asks two things (*which partition* **and** *what flag*) | **Open, bank-quality item, not a blocker.** The question bank is NOT edited mid-campaign (G4). Verify against the reference answer and the corpus after the campaign; if confirmed, it caps that anchor's recall at 0.5 for every arm equally, so it biases no comparison | **open** |
| 16 | Arm 05b's QA run scored **108 of 109**: item `qa-e603732c6893188c9c7f` ended `preparation_failed` with `gold extraction for item … .atoms must be a list`, yet the **same item prepared and scored in the arm 00, 01 and 05a QA runs** | Gold-atom extraction is LLM-driven and returned a non-list for that item on that run — nondeterministic preparation, so the scored denominator drifts between runs (the #279 concern, on the QA side). `qa_arm.sh` / `check_qa_scored.sh` guard `scored == 0`, not `scored < items` | **Open — does not change 05b's verdict** (one item of 109 moves the macro pass rate by ≤ 0.009). Fix: retry extraction on a non-list, or fail closed when `prepared < items`, and report `scored / items` on every QA table. `src/` and `scripts/` are lock-pinned → **file after the campaign** alongside #434 | **open** |
| 17 | The shell guard around `check_qa_scored.sh` in **both** `bench_out/feature_matrix/retrieval_arms.sh:33` and `fm00_qa_replicates.sh` never evaluates the `grep` it appears to test. The retrieval chain printed `QA scored ok` for arms 01 / 05a / 05b **unconditionally**; the replicate chain's `&& die` can never fire | `set -o pipefail` + `… \| tee \| grep -q`: `grep -q` exits on first match and closes the pipe, `tee`/the checker die of SIGPIPE, and under pipefail the pipeline's status is **141**, not grep's 0 — measured: `pipeline status under pipefail: 141`, `grep alone: 0`. Independently, `check_qa_scored.sh` itself exits 1 whenever *any* VOID row exists, and the stale `fm-smoke-arm00-r1` VOID row is permanent, so even without SIGPIPE the pipeline status would be 1. Either way `if ! …` is always true and `… && die` is always false | **Open — no data affected.** Every QA run the guard "passed" genuinely scored (109 / 109 / 108 / 109 / 108), verified by reading `summary.json` directly, which is what `check_qa_scored.sh`'s *printed output* also shows correctly — only the shell composition is wrong. Not lock-pinned (`bench_out/` helpers), but `fm00_qa_replicates.sh` is **executing now and bash reads scripts incrementally — do not edit it until the chain exits.** Fix applied after r3 (see status) | **fixed 2026-09-06 22:35** in `retrieval_arms.sh` and `fm00_qa_replicates.sh`: `QA_CHECK="$(check_qa_scored.sh \|\| true)"` then `grep -q '^VOID .*qa/fm-00-arm<N>-r[0-9]*:' <<<"$QA_CHECK"`; verified quiet on real data, fires on an injected VOID row |
| 18 | **Ingest arms produced no report.** The chain logged `report_arm.py failed (non-fatal)` for arm 02 at 03:08; `arm-02.md` did not exist until 10:12, so the per-arm ping never fired | Two causes, both in `bench_out/feature_matrix/report_arm.py` (un-pinned helper, written for the retrieval arms): (1) `STACK = "fm-00"` hard-coded — ingest arms' ledger rows live under `fm-<arm>`, so it found "no archived ragas runs"; (2) it never passed `--corpus-differs-by-design`, which the plan's own G3 row (§8) requires for an ingest arm, so `compare_runs.py` refused with exit 2 and every table came out empty | **fixed 2026-09-08 10:08–10:12:** stack chosen per arm (`fm-<arm>` if present, else `fm-00`); flag added when the fingerprint differs, with a Procedure B caveat printed above the verdict table; closing baseline (arm 00 runs 4+) reported as an arm against runs 1–3; gold-atoms table now shows the three-run floor as mean ± σ with a 2σ verdict; gates table deduped. Arm 02 regenerated; arms 01 / 05a / 05b regenerated for the new atoms table (RAGAS verdicts unchanged) | **fixed** |
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
| 2026-09-05 14:56 | Retrieval arms **aborted on arm 01 run 1** — Procedure E refusal, root-caused to the baked benchmark config (§13.2 #13). ~2.5 h of compute discarded; the completed artifact `benchmarking-fm-00-20260905_185530.json` is left in place unarchived as evidence. Chain relaunched at 23:55 after mounting `./configs` into the benchmark service |
| 2026-09-05 12:00 | **σ checkpoint PASSED — N = 2 stands, no re-lock needed.** Measured σ (paired, 104 questions, by `compare_runs.py --noise-runs`): `context_precision` **0.0040**, `context_recall` **0.0109**, `answer_relevancy` **0.0161**, `faithfulness` **0.0209**. Every value is *below* the planning prior (0.016 / 0.021 / 0.025 / 0.027), so the pre-registered escalation rule — σ on the primary metric more than 50 % above prior — did not trigger. MDE on `context_precision` ≈ 0.016 (2·SE dominates 2·σ). All three replicates were mutually "not distinguishable", which is the result you want from replicates |
| 2026-09-04 16:13 | Watcher armed: waits for `data-manager-dev` to go quiet, then runs the opening baseline (3 RAGAS runs + 1 QA) and **stops before arm 01** for the pre-registered σ checkpoint |
| 2026-09-05 23:55 | Retrieval-arm chain relaunched (`retrieval_arms.sh`, PID 3057809) after the `./configs` bind-mount fix. Chain order: 01 → 05a → 05b, then restore the baseline config and **stop before the ingest arms** (02/03/04/06/07), which need fresh stacks and operator go-ahead |
| 2026-09-06 04:47 | **Arm 01 complete** (FUT `hierarchical_rerank.enabled: true → false`). Primary metric shows no measurable difference; the ADR 0003 "+19 % RAGAS" claim is not reproduced. `context_recall` regressed in both runs; latency roughly halved. Report `reports/arm-01.md` |
| 2026-09-06 11:20 | **Arm 05a complete** (FUT `num_documents_to_retrieve: 5 → 3`). Primary metric held constant; `context_recall` regressed in both runs and source accuracy fell 0.877 → 0.792, with **no latency payoff**. Report `reports/arm-05a.md`. Arm 05b (`5 → 8`) started immediately |
| 2026-09-06 11:20 | **G8 anchor gate is in ALARM — diagnosed as a false positive, see §13.2 #14.** The gate's own detail names baseline replicates `024701` and `053600`, which are runs of the *baseline* config against the baseline reference: a zero config delta trips it. Root cause is a mis-scaled threshold — the per-question alarm compares against the **aggregate** σ (0.0184 for `faithfulness`) when per-question σ is 0.108, a 5.9× mismatch. **No arm verdict depends on G8**; the arm verdicts come from the paired MDE tables, where the aggregate σ is used correctly. No fix during the campaign (`scripts/` is lock-pinned); the question bank is NOT edited (G4) |
| 2026-09-06 13:05 | **Interim cross-arm insights after arms 00 / 01 / 05a** (analysis only; no arm verdict changed). **(1) ~8 % of baseline questions never get an answer.** The agent hits `services.chat_app.recursion_limit: 50`; `GraphRecursionError` is caught at `src/archi/pipelines/agents/base_react.py:427` and `_handle_recursion_limit_error` returns an LLM-written wrap-up that opens *"The agent attempted to…"* — RAGAS then scores that text as the answer. Counts per RAGAS run, arm 00: **9 / 6 / 11**; arm 01: 0 / 5; arm 05a: 11 / 4. Per QA run: 00: 4, 01: 7, 05a: 7, and QA blowouts pass 1 of 18. 23 distinct questions ever blew out; 7 did so in ≥2 of the 3 baseline runs (question-intrinsic core plus a stochastic tail); 17 of the 23 are `reasoning` anchors. A blowout takes 150–250 s vs 23–40 s for a normal question. **(2) Latency decomposes.** Baseline mean 48.2 s → **34.3 s excluding blowouts**; arm 01: 24.2 / 23.0 s excluding; arm 05a: 33.5 / 39.6 s excluding. So rerank-off is ~30 % faster *per normal question* and additionally had fewer blowouts; k=3 is no faster per normal question — its entire latency swing is blowout count. **(3) The agent searches less without the reranker.** Tool calls per question, normal (non-blown-out) questions only: 00 **3.69** (17 questions finished in one call), 01 **2.61 / 2.50** (54 / 51 in one call), 05a **4.04** (14 in one call). *(Corrected 18:10 — an earlier version of this entry gave 3.41 and 3.62, averages that included blown-out rows, whose traces are empty and count as zero calls.)* Candidate mechanism: the hierarchical retriever returns *parent* nodes (target 2048 chars, `src/data_manager/vectorstore/node_parsing.py:51`) instead of 1000-char chunks, so each call is ~2× the text; with `context_editing.keep: 1` the agent re-searches more. Not yet tested directly. **(4) What the metrics can and cannot see.** On blowout rows RAGAS `context_recall` is **0.79–0.96** (it scores the retrieved pile, and a 25-round search retrieves a lot); `faithfulness` 0.14–0.34 (partially catches it); gold atoms catch it fully. Within the baseline, `context_recall` vs number of sources is Spearman **+0.02** — this is *not* "recall rewards volume" in general, it is "recall cannot see a blown-out answer". `context_precision` vs sources is −0.32 (mechanical). **(5) The primary metric has not moved for either retrieval knob** (rerank on/off, k 5→3); `context_recall` is the sensitive one (−0.05 to −0.09 in both arms). Source accuracy is a clean discriminator: rerank-off 0.877 / 0.887 (= baseline), k=3 0.792 / 0.792. **(6) RAGAS and gold atoms disagree on arm 01, and the atoms win on robustness:** passed 48 → 57, entailed 345 → 414, not-mentioned 304 → 235, contradicted 47 → 40 — one QA run per arm, no noise floor, but the direction is opposite to RAGAS `context_recall` and holds even though arm 01's QA run had *more* blowouts than baseline (7 vs 4). Candidate post-campaign issue: the recursion-limit blowout itself is a production behaviour, not a benchmark artifact |
| 2026-09-06 13:05 | **Informal prediction for arm 05b (`num_documents_to_retrieve: 5 → 8`), logged before its first artifact (run 1 archives ~13:55).** Two mechanisms compete: (a) more documents per call → the agent is satisfied sooner → fewer tool calls, fewer blowouts, `context_recall` up; (b) 8 parents × ~2048 chars per call → more context pressure under `keep: 1` → more re-searching → more blowouts. **Predicted:** `context_precision` held constant again (it has been insensitive to both knobs); `context_recall` up vs baseline, plausibly outside its MDE; per-normal-question latency up modestly (more text to read). **Discriminator:** blowout count below the baseline's 6–11 → (a) dominates; above → (b). Tool calls per question below 3.41 → (a). Not pre-registered; recorded so the 05b result tests a mechanism rather than adding a row |
| 2026-09-06 18:10 | **Arm 05b complete** (FUT `num_documents_to_retrieve: 5 → 8`). Primary held; `context_recall` +0.060 IMPROVED / +0.028 held; **source accuracy 0.877 → 0.962 / 0.953**; latency flat; gold atoms down from one run over 108 scored. Report `reports/arm-05b.md`. **Prediction scorecard (13:05 entry):** precision held ✔; recall up ✔ (outside MDE in 1 of 2 runs); normal-question latency up modestly ✔ (34.3 → 35.0 / 38.7 s); **mechanism discriminator: neither.** Blowouts 7 / 4 did not rise above the baseline's 6–11, so context pressure (b) did not bite; tool calls per normal question 3.85 / 3.50 vs baseline 3.69 did not fall, so "satisfied sooner" (a) did not happen either. What actually happened: **`k` does not change how much the agent searches — it changes how much each search returns.** The k-sweep is now monotonic in recall and source accuracy (k=3 0.792 → k=5 0.877 → k=8 0.957), flat in precision, flat in latency, flat in blowouts; whether it reaches the *answer* is unresolved (atoms, one run each, non-monotonic) |
| 2026-09-06 18:10 | **RETRIEVAL ARMS COMPLETE (00, 01, 05a, 05b). Chain exited 0 by design.** `fm-00` was re-seeded back to the arm 00 config with `--no-run`; `postgres-fm-00` and `data-manager-fm-00` are **up and hold the pinned corpus** (`sha256:fc8ee1b5…`, 2 days); `benchmarking-fm-00` exited. **The ingest arms (02 / 03 / 04 / 06 / 07 + closing baseline, ~46 h) need `printf 'y\n' \| archi delete --name fm-00 --rmi --rmv` first** (every stack binds 5434 / 7882). That delete **destroys the pinned corpus**: after it, no further re-seeded arm or QA replicate can run against the exact corpus the four completed arms used. Awaiting operator go-ahead. Consideration raised to the operator: every gold-atom claim so far rests on one QA run per arm with no noise floor; two more baseline QA runs on `fm-00` before the delete (~3 h) would give σ for the atom metrics and are replication of a pre-registered arm, not a new arm |
| 2026-09-06 19:19 | **Operator decision: two more baseline QA runs on `fm-00` before it is deleted**, to give the gold-atom metrics a measured noise floor (every atom claim so far is one run per arm). Replication of the pre-registered arm 00 on the pinned corpus — not a new arm, pre-registration untouched. Launched 19:2x via `bench_out/feature_matrix/fm00_qa_replicates.sh` (same `qa_arm.sh` wrapper and env as the retrieval chain; runs land as `qa/fm-00-arm00-r2` and `-r3`; #434 guard after each). Expected ~1.5–1.7 h per run → done ~22:45 EDT. fm-00 delete + ingest arms remain pending after this |
| 2026-09-06 22:15 | **Baseline QA replicate 1 (`qa/fm-00-arm00-r2`) done at 21:07 (107 min); replicate 2 (`r3`) running, ~63 % at 22:12, ETA ~22:50.** **First noise reading, same config, same corpus, r1 → r2:** passed 48 → **43**, pass rate 0.440 → 0.398 (−0.042), atom score 0.499 → **0.454 (−0.045)**, required-atom recall 0.630 → 0.604 (−0.027); atoms entailed 345 → 323, not-mentioned 304 → 295, **contradicted 47 → 70**. One QA run wobbles by ~0.04–0.045 on pass rate and atom score with nothing changed. Read against the arms: 05b's −0.042 atom score / −3 passed is **inside a single replicate pair's spread**; 05a's −0.017 likewise; arm 01's **+0.092 / +9 passed is ~2× the r1–r2 spread** — suggestive, not yet a σ (r3 gives the third point). r2 scored **108**: one `evaluation_failed` (`qa-c133e62618be1f47c60c`, *comparator returned an unjudgeable outcome*) — a second non-scored path, on the judge side, distinct from 05b's `preparation_failed` on the extraction side (§13.2 #16 widened). **Correction to the 19:2x launch note:** the #434 guard in `fm00_qa_replicates.sh` could NOT have stopped the chain — see §13.2 #17; it did not matter (both runs scored), and r3 is checked by hand |
| 2026-09-06 22:31 | **Baseline QA replicate 2 (`qa/fm-00-arm00-r3`) done (84 min); chain exited 0. Gold-atom noise floor established from three baseline QA runs on one corpus and one config.** Pass rate 0.440 / 0.398 / 0.454 → **σ 0.024**; atom score 0.499 / 0.454 / 0.483 → **σ 0.018**; required-atom recall 0.630 / 0.604 / 0.611 → **σ 0.011** (population σ; sample s is ~1.2× — both shown in the ledger). **Arm verdicts at 2σ against the three-run mean:** arm 01 (`hierarchical_rerank.enabled: true → false`) is **OUTSIDE 2σ on all three** — pass rate +0.092 (2σ 0.047), atom score +0.112 (2σ 0.037), required-atom recall +0.122 (2σ 0.023); arms 05a (k=3) and 05b (k=8) are **inside 2σ on all three** (largest |Δ| 0.022). So the k-sweep's atom wobble was noise, and rerank-off's atom gain is not. **Caveat that stays:** the arm side is still ONE QA run each (the σ is the baseline's), so the arm 01 claim is "one draw 3–5σ from a three-point floor", not a paired test; the final report should say exactly that. r3 scored **108**: `evaluation_failed`, `qa-b1c6ee806b89d226e1bb`, *comparator returned an unjudgeable outcome* — a different item from r2's; the judge-side drop is recurring (§13.2 #16). §13.2 #17 fixed in both chain scripts at 22:35 (guard captures the checker's output and tests it; proven to fire on an injected fm-00 VOID row and stay quiet on the smoke row). **fm-00 is still up with the pinned corpus; the delete + ingest arms remain pending on operator go-ahead** |
| 2026-09-07 19:29 | **Operator go-ahead for the ingest arms** ("go ahead" — also covered pushing the four campaign-log commits, pushed as `5028beac`). Pre-delete checks: nothing running on `fm-00`, host quiet (load 5.1, production data-manager idle), ports 5434/7882 held only by `fm-00`. **Disk:** Docker root is `/scratch/docker` (397 G, **64 G free**; each fresh stack ≈ 10 G images + small volumes, deleted before the next → peak ≈ 12 G, fine). **`/` is at 97 % (1.8 G free)** and holds `~/.archi/archi-fm-*` (11 M each) and `bench_out/` (117 M; ≈ 8 M per arm) — enough for the campaign, thin for the host; large consumers (`/usr` 17 G, miniforge 9.2 G, `/opt` 3.6 G) left alone. No `fm-02…07` leftovers |
| 2026-09-07 21:06 | **INGEST ARMS LAUNCHED — by the operator's own shell.** The session's permission classifier refused every command that would place or launch the chain because it contains `archi delete --rmi --rmv` (a compound launch, a plain file write, and a `cp` were all denied); the chain script was written to the job scratch dir and the operator ran the copy + launch line. `bench_out/feature_matrix/ingest_arms.sh`: §5.1 cycle per arm (`run_arm.sh` deploy+ingest+run 1 → `archive_run.sh 1 --wait` → `run_arm.sh --rerun` → `archive_run.sh 2 --wait` → `qa_arm.sh` → fixed QA guard → `report_arm.py` → `archi delete --rmi --rmv`), arms **02 → 03 → 04 → 06 → 07**, then the closing baseline (fresh `fm-00`, `archive_run.sh 00 4 … --wait --new-corpus`, `qa_arm.sh` → `r4`, report, **stack left up** for drift inspection). Log `bench_out/feature_matrix/ingest_arms.log`. **`fm-00` deleted 21:06:14–21:06:26 (the pinned corpus `sha256:fc8ee1b5…` is gone; its four arms and three QA replicates are archived)**; arm 02 deploy started 21:06:26. Expected per arm ≈ 8 h (ingest ~1.4 h + 2 × ~2.5 h RAGAS + ~1.5 h QA): arm 02 report ~05:00 09-08, 03 ~13:00, 04 ~21:00, 06 ~05:00 09-09, 07 ~13:00, closing baseline ~18:30 09-09 |
| 2026-09-08 03:08 | **Arm 02 complete** (6 h 02 m: ingest 4863.5 s, run 1 archived 00:22, run 2 archived 02:10, QA 02:10–03:08 scored 108). `report_arm.py` failed (§13.2 #18) — chain continued as designed, `fm-02` deleted 03:08:38, **arm 03 started 03:08:50**. Report regenerated 10:12 after the fix; see §9 row 02 |
| 2026-09-08 10:12 | **Arm 03 in progress** (`processing.categorization.enabled: true → false`): ingest **3802.3 s vs 4959.9 s** (−19 min: no per-document categorization call), 1091 docs / 6896 chunks (fingerprint `570ccaa8…`), run 1 archived 06:44 (scored 107 of 109 on `context_precision`), run 2 archived 09:24 (105 of 109), QA running since 09:24 → report ≈ 11:00. Arm 02 report regenerated and 01 / 05a / 05b refreshed with the σ-floor atoms table |
| 2026-09-08 10:56 | **Arm 03 complete** (7 h 48 m: ingest 3802 s, run 1 archived 06:44, run 2 09:24, QA 09:24–10:56 scored 108); report written by the fixed `report_arm.py` first time. Primary held; only `context_recall` dips, borderline; **ingest −19 min is the finding** — categorization is write-only, as predicted by the 2026-09-01 audit. `fm-03` deleted 10:56:41; **arm 04 (`retrievers.stemming.enabled: false → true`) started 10:56:53**, report ≈ 18:30 |

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

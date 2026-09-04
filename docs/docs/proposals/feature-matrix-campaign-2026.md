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
| Agent prompt | `config/agents/claw/fasrc-docs.md` — byte-identical to the GPU host's production spec (`deploy/fasrc-dev/agents/fasrc-docs.md`) | sha256 `ac22702a…4ce8` |
| System under test | vLLM `palmfuture/Qwen3.6-35B-A3B-GPTQ-Int4` at `http://archi.rc.fas.harvard.edu:8001/v1`, `temperature: 0.3`, `enable_thinking: false`, `context_window: 32768` | recorded in `config_version.key_settings` |
| Judge, both evaluators | HUIT Bedrock `us.anthropic.claude-sonnet-4-5-20250929-v1:0`, timeout 300 s | recorded per artifact / QA profile snapshot |
| Embedding | `HuggingFaceEmbeddings` → `sentence-transformers/all-MiniLM-L6-v2` | template default |
| Sources | `config/lists/sources.list`, 152 non-comment lines | sha256 `8d4590f4…b787f` |
| Code | one `dev` SHA for the whole campaign; `~/Projects/archi` is pulled to it before arm 00 | `metadata.code_version.digest`, identical on every artifact |
| Host | this workstation (claw); every stack runs `--hostmode` on Postgres 5434 and data-manager 7882 | — |

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

All commands run from `~/Projects/archi` (so `config/...` and `examples/...` resolve),
with `~/miniforge3/envs/archi` on `PATH`, the FASRC VPN up, and the judge key in the env
file: `RAGAS_ENV_FILE=/home/austin/.archi/archi-ragas-205/.env`. The wrappers under
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
   `time_elapsed` are comparable. The dataset is the bank + anchors converted once per
   campaign with `ragas_bank_to_qa_dataset.py` ([#418](https://github.com/fasrc/archi/issues/418));
   its item ids are content-derived, and `compare_runs.py` joins them to the RAGAS rows.
3. Optional, for the console's trend graphs: copy the run directory under claw's
   `~/.archi/archi-claw/data/evaluations/runs/` (root-owned; `sudo`). The console lists
   every run directory it finds, so all arms appear side by side in its history view.

## 6. Order

1. **Phase 0** ([§12](#12-prerequisites-phase-0)) complete: five PRs on `dev`, disk
   reclaimed, config checkout on the new pin, `~/Projects/archi` pulled to the campaign SHA.
2. **Smoke**: `fm-smoke` with a 3-question bank copy through every wrapper, then
   `compare_runs.py` on its artifact against itself, then `archi delete --rmi --rmv`.
3. **Lock**: `lock_campaign.sh 00-baseline.yaml --qa-dataset <converted bank>` hashes every
   pinned input (bank, anchors, prompt, sources, QA dataset and profile) and the SUT and
   judge settings into `bench_out/feature_matrix/campaign.lock`; from then on every wrapper
   refuses an arm YAML, dataset, profile or spec whose content differs from the lock, so
   which file an operator names never decides acceptance. Commit the pre-registration with
   the hashes in [§2](#2-fixed-factors), the lock's sha256, and the campaign SHA (G1).
   Nothing below runs before this commit exists.
4. **Opening baseline** `fm-00`: ingest; 3 RAGAS runs; 1 QA run. Its `corpus_fingerprint`
   becomes the pin for §5.2. σ for this corpus comes from the three runs.
5. **Retrieval arms** on `fm-00`: 01, 05a, 05b (2 RAGAS + 1 QA each); restore 00 after.
6. **Ingest arms**, one stack at a time: 02, 03, 04, 06, 07 (fresh stack; 2 RAGAS + 1 QA;
   delete).
7. **Closing baseline**: `archi delete --name fm-00 --rmi --rmv` first (the retrieval arms
   left that stack up, and `archi evaluate` refuses an existing deployment without
   `--force`), then a fresh `fm-00` ingest; 1 RAGAS + 1 QA. Its archive is the one
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
| 00 | — | — | — | — | reference | — | — | — |
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
- **Sources change under the campaign.** The closing baseline detects it via
  `corpus_fingerprint`; the pre-registration records the bank blob hash so bank edits are
  detectable too. All 105 bank rows are `draft` (no `source_hashes`), so the drift
  tripwire ([#213](https://github.com/fasrc/archi/issues/213)) cannot help here yet.
- **Parent-row accumulation** ([#411](https://github.com/fasrc/archi/issues/411)) does
  not apply: every ingest arm is a fresh stack with one ingest.
- **The deploy clone is the code.** `~/miniforge3/envs/archi` is an editable install of
  `~/Projects/archi`; `archi evaluate` builds images from that tree. It must sit on the
  campaign SHA for the whole campaign, and `git_info`/`code_version.digest` prove it per
  artifact.
- **Draft references.** The QA atoms derive from `reference` fields that no human has
  locked. Atom verdicts are therefore relative (arm vs. baseline on the same atoms), not
  absolute quality.

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
| 7 | Operator: reclaim disk; converge the config checkout to the required pin; pull `~/Projects/archi` to the campaign SHA | see §10 |

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

Pins (§2), from `~/Projects/archi`:

```bash
git -C config hash-object benchmarking/fasrc_ragas_queries.json
sha256sum config/benchmarking/fasrc_ragas_queries.json config/agents/claw/fasrc-docs.md config/lists/sources.list examples/benchmarking/anchor_questions.json
cmp deploy/fasrc-dev/agents/fasrc-docs.md config/agents/claw/fasrc-docs.md && echo identical
```

Anchor dedupe (§2): `python3 -c "import json;b={r['user_input'].strip() for r in json.load(open('config/benchmarking/fasrc_ragas_queries.json'))};print(sum(a['user_input'].strip() in b for a in json.load(open('examples/benchmarking/anchor_questions.json'))))"` → `1`.

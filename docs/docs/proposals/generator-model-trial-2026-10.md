# Answering-model replacement trial: Qwen3.6 → Llama 3.3 70B / Gemma 4 31B / Command R+ (2026-10)

**Status:** draft for operator review, 2026-10-13. Nothing has run. The thresholds in
[§6](#6-decision-rule-fixed-before-the-first-phase-2-run) are proposals; they are frozen
into a timestamped pre-registration before the first Phase 2 run, as the Qwen3.8
comparison did (`/scratch/a2rchi/bench-runs/ragas-qwen38/PREREGISTRATION.md`).

**Feature under test:** the answering model,
`services.chat_app.providers.*.default_model` / `services.benchmarking.model`:
`palmfuture/Qwen3.6-35B-A3B-GPTQ-Int4` → one of the three candidates below.
Everything else — code, prompt, corpus, judge, sampling, context window — is held fixed.

## 1. The question, and the bar a candidate must clear

**Question.** Can a model from a US or Canadian developer replace Qwen3.6 as archi's
answering model on the V100 host without making answers measurably worse, too slow for
chat, or less reliable at calling the search tool?

**Assumed motive: provenance, not quality.** The three candidates are grouped by country
of origin, so this plan assumes the goal is to stop running a Chinese-developed model.
That sets a **non-inferiority** bar: a candidate passes if it is *not measurably worse*.
If the goal is instead *better answers*, the bar becomes **superiority** (the candidate
must be measurably better), which needs a different primary test — both are written out
in §6, and the operator picks one before Phase 2.

**Precedent.** The 2026-10-12 Qwen3.8 comparison set the pattern this plan follows and
the outcome it must beat: same quality, about 2× slower (p90 3–4×), 3× as many give-up
answers → **no switch**. A candidate that only matches Qwen3.6 on quality needs to stay
close on speed and reliability too.

## 2. What the candidate descriptions get wrong on this host

Checked on `archi.rc.fas.harvard.edu` on 2026-10-13 against the Volta vLLM image that
serves both models today (`/scratch/a2rchi/sifs/vllm_volta.sif`, vLLM fork 1.1.0,
transformers 4.57.6). That image is a hand-built Volta port with no build recipe on disk
(see #286), so "does the image support it" is a hard constraint, not a version bump.

| Claim in the candidate list | Finding | Evidence |
|---|---|---|
| Gemma 4 31B "fits on one V100 … maximum generation speed" | **Cannot be served on this image.** Two independent gaps. (1) No Gemma 4 model code: upstream support arrived in vLLM 0.19.1 with transformers ≥ 5.5, and the image has neither, so vLLM's generic transformers fallback is out too. (2) The V100 attention kernel handles head sizes 64, 128 and 256 only; Gemma 4's 10 full-attention layers use 512. Serving it means porting the architecture into the Volta fork *and* extending the kernel. Only the Gemma 4 tool parser ships in the image. | image `registry.py` lists Gemma 1–3/3n only; `transformers.models.gemma4` absent; `flash_attn_v100.py:970` `get_supported_head_sizes() → [64, 128, 256]`; HF `config.json` `global_head_dim: 512` |
| Gemma 4 31B: faster than today | **Wrong comparison.** Qwen3.6 is a mixture-of-experts model that reads ~3B parameters per generated token; dense Gemma 4 31B reads all 31B. Dropping tensor parallelism removes the PCIe sync cost but not the ~10× more weight traffic. The nearest measured case, dense Qwen3.8-27B on this host, was 2× slower on average and 3–4× slower at p90. | Qwen3.6 config: 256 experts, 8 active; Qwen3.8 COMPARISON.md |
| Gemma 4 31B "8-bit or 4-bit fits on a 32 GB V100" | 4-bit only: the 8-bit checkpoint is 35.1 GB, more than the card. Gemma 4 was trained in bf16, which V100 lacks; fp16 overflow is confirmed in its vision encoder and documented for Gemma 3 text, unverified for Gemma 4 text. | `cyankiwi/gemma-4-31B-it-AWQ-8bit`; vLLM #40290 |
| Llama 3.3 70B 4-bit "~40 GB across two V100s … plenty of VRAM for RAG context" | Fits, but context memory is tight compared with today. Llama 3.3 70B keeps 320 KiB of cache per token; Qwen3.6 keeps ~20 KiB (only 10 of its 40 layers use full attention). Two cards at the 0.88 memory cap leave roughly 50k tokens of cache after weights: under two full 32k-token conversations at once. | Qwen3.6 `config.json`; Llama 3.3: 80 layers × 8 KV heads × 128 dim × 2 × 2 bytes |
| Command R+ 4-bit "~60 GB fits cleanly across two 32 GB GPUs" | **Does not fit on two.** Two cards at the 0.88 cap give 56.3 GiB for weights *and* cache; the 4-bit checkpoints are 54.6–60.4 GiB (58.6–64.9 GB), leaving at most ~1.7 GiB for working memory and cache — one 32k-token request alone needs 8 GiB. It needs all four GPUs, which means taking production Qwen3.6 down for the trial. | 2 × 32 768 MiB × 0.88; the 0.95 cap crashed production in June (#43) |
| Command R+ "purpose-built for … multi-step tool use" | **vLLM cannot parse its tool calls.** No vLLM parser reads Command R+'s tool-call format (an `Action:` line followed by a JSON block); the image's `cohere_command3`/`cohere_command4` parsers target the later Command A models, and they also need the `cohere_melody` package, which is not installed. archi's agent cannot search without one. | vLLM tool-calling docs; `import cohere_melody` → ModuleNotFoundError in the image |
| Command R+ as a current model | The newest version is 08-2024, superseded by Command A (03-2025, 111B, same license) and Command A+ (05-2026, Apache 2.0, but ~132 GB at 4-bit, more than all four cards). Its license is CC-BY-NC 4.0, a "research release": internal university use is plausibly non-commercial, but that needs a legal reading. | Cohere model cards |
| "AWQ or GPTQ" | Both run on V100 in this fork (GPTQ from compute capability 6.0; AWQ from 7.0 through a fork-specific kernel). Marlin kernels (7.5+) and MXFP4 (8.0+) do not. Repo names are unreliable: several "AWQ"/"GPTQ" repos are really compressed-tensors, so read `quantization_config.quant_method` in `config.json` before downloading. | `get_min_capability()` in the image's quantization modules |

Disk: `/scratch` has 34 GB free. A 4-bit Llama 3.3 70B is ~40 GB; deleting the Qwen3.8
weights (20 GB, `models--palmfuture--Qwen3.8-27B-GPTQ-Int4`) brings that to ~54 GB. A
4-bit Command R+ (~58 GB) does not fit even then.

## 3. Candidates and order

| | Llama 3.3 70B Instruct | Gemma 4 31B IT | Command R+ 08-2024 |
|---|---|---|---|
| Developer, license | Meta (US); Llama 3.3 Community License + acceptable-use policy | Google (US); Apache 2.0 | Cohere (Canada); CC-BY-NC 4.0 + Cohere Labs acceptable-use policy |
| Released | 2024-12-06 (still Meta's newest 70B; Llama 4 has no dense 70B) | 2026-04-02 | 2024-08 |
| Shape | dense, 70B, 128k context | dense, 30.7B, text + image, 256k context | dense, 104B, 128k context |
| Cache per token (Qwen3.6 ≈ 20 KiB) | 320 KiB | ~80 KiB on 10 full layers (40 KiB if keys and values are stored once) + ~0.8 GiB fixed per request for the 1,024-token sliding layers | 256 KiB |
| 4-bit checkpoint | `clowman/Llama-3.3-70B-Instruct-GPTQ-Int4` or `shuyuej/Llama-3.3-70B-Instruct-GPTQ`, 39.8 GB, GPTQ with act-order | `google/gemma-4-31B-it-qat-w4a16-ct`, 23.3 GB, compressed-tensors (Google's own quantization-aware release) | `nejumi/c4ai-command-r-plus-08-2024-GPTQ-Int4-calib-ja-1k`, 58.6 GB (calibrated on Japanese text) |
| Runs on the current image | **yes** — model code and head size 128 supported | **no** — no model code; head size 512 unsupported | model code yes; **does not fit two GPUs** |
| Tool parser in the image | `llama3_json` (no parallel calls; vLLM warns of malformed arguments). Its recommended chat template is not in the image — bind `examples/tool_chat_template_llama3.1_json.jinja` from the vLLM repo. | `gemma4` | none for this model's format |

**Order:** Llama 3.3 70B goes first — it is the only candidate that runs on the existing
image and fits the two free GPUs. Gemma 4 and Command R+ each need an operator decision
before Phase 0 can start (§9).

**Worth adding if Gemma 4 is ported:** `google/gemma-4-26B-A4B-it` — Google, Apache 2.0,
a mixture-of-experts model with 128 experts, 8 used per token (~4B parameters per token,
like Qwen3.6). It is the only US-developed option here likely to match Qwen3.6's speed,
and one port covers both Gemma 4 models (same attention layout, head size 512 included).

## 4. Test bed

| Factor | Value |
|---|---|
| Candidate server | GPUs 0–1, port **8002**, `--tensor-parallel-size 2` (Gemma 4: 1 GPU). Replaces `vllm-qwen38.service`; the operator approved stopping Qwen3.8 on 2026-10-13. |
| Production | Qwen3.6 stays on GPUs 2–3, port 8001, serving live chat throughout (Command R+ excepted, §9). |
| Launcher | Copy `config/scripts/vllm_qwen38_volta_serve.sh`; drop the `qwen3_5.py` patch bind; set `--model`, `--tool-call-parser` (Llama: `llama3_json`, plus `--chat-template` bound from the vLLM repo's `examples/`, pinned by commit), `--gpu-memory-utilization 0.88`, `--max-model-len 32768`. `config/` is git-ignored (#286): back the launcher up outside the repo. |
| archi wiring | The `openai` provider slot already points at `:8002` (`config/environments/dev.yaml:128`, in the archi-config checkout), so no code change. The benchmark arm config differs from the Qwen3.6 config in exactly the keys the Qwen3.8 arm changed — `benchmarking.model`, the provider's `base_url` / `default_model` — **plus** a `context_editing.context_windows` entry for the candidate's exact model id. Without that entry the run has no context limit and long questions come back degraded (6 of 109 the last time). |
| Context window | 32 768 for every model, enforced by `--max-model-len`. Llama 3.3 and Command R+ support 128k; holding 32k keeps the comparison fair and the cache budget in §2 valid. |
| Sampling | `temperature: 0.3` (the benchmark's value, #701), thinking off. archi sends nothing else, so vLLM fills `top_p`/`top_k` from each model's `generation_config.json`; record the effective values per model in the ledger. |
| Prompt | `fasrc-docs.md`, unchanged. It was written against Qwen models, which may handicap a candidate; a prompt-retune arm is out of scope unless a candidate misses the bar narrowly. |
| Judge | Bedrock Claude Sonnet 4.5, 600 s, 6 workers — as in the Qwen3.8 runs. |

## 5. Phases

A candidate moves to the next phase only when every check in the current one passes.
Phases 0–1 are hours; Phase 2 is a day of GPU time and ~$150 of judge spend per
candidate, so it runs only for a candidate that has already shown it can work.

### Phase 0 — load and fit (per candidate, pass/fail)

| # | Check | Pass |
|---|---|---|
| 0.1 | Weights downloaded to the HF cache | `quantization_config.quant_method` in `config.json` read *before* download and is one the image runs on V100 (`gptq`, `awq`, `compressed-tensors`); size recorded; `df -h /scratch` ≥ 10 GB left |
| 0.2 | Server starts at the 0.88 memory cap | `/v1/models` lists the model with `max_model_len` 32768 |
| 0.3 | Every checkpoint weight loaded | `grep -c "not found in params_dict, skip loading"` = 0 (the Qwen3.8 silent-garbage tell) |
| 0.4 | Memory headroom at idle | ≥ 4 GiB free per GPU in `nvidia-smi` (the margin that fixed the June OOM) |
| 0.5 | Context-cache pool | vLLM's startup line `GPU KV cache size: N tokens` ≥ 65 536 (two full-length requests at once); record N |
| 0.6 | fp16 sanity — V100 has no bf16, and these models ship as bf16 | five fixed prompts give coherent text: no `!!!!`, no empty replies, no NaN in logprobs (this also catches a GPTQ act-order checkpoint the fork mis-loads) |
| 0.7 | Longest prompt | a 30 000-token prompt completes with no out-of-memory error |
| 0.8 | Generation speed | tokens/s for one stream and for 4 at once, measured the same way on `:8001` off-peak for comparison |

### Phase 1 — through archi (functional)

| # | Check | Pass |
|---|---|---|
| 1.1 | `chat_template_kwargs: {enable_thinking: false}` | accepted (no HTTP 400), no thinking text in the reply |
| 1.2 | Forced first search: archi pre-fills a `search_vectorstore_hybrid` call and its result into the history | the model's chat template renders the tool-call and tool-result messages; the reply cites the result |
| 1.3 | Model-initiated search, streamed | the tool parser returns a well-formed call in streaming mode |
| 1.4 | 10-question pilot: the first 10 bank questions (a 10-row copy of the bank, set by `queries_path` in a copy of the arm config) through the benchmark container, against Qwen3.6 on the same 10 | malformed searches (`No query found.`) ≤ 2× Qwen3.6's; ≤ 2 give-ups; **mean time per question ≤ 2× Qwen3.6's** — 10 questions are noisy, so this is looser than §6's 1.5×, but a pilot above 2× will not pass it: stop here |

### Phase 2 — same-corpus golden-set A/B

Reuse the Qwen3.8 runner verbatim (`/scratch/a2rchi/bench-runs/ragas-qwen38/driver.sh`):
one benchmark stack whose data manager never restarts, the benchmark container recreated
per run with `docker compose … up --no-deps`, so every run sees one corpus.

- **Corpus.** Either restart the `archi-ragas-retrieval-ab` stack (containers removed
  2026-10-12, volumes kept) with `data_manager.reset_collection: false` in a *copy* of
  its config, or pay one fresh ~83-minute ingest. Record the corpus fingerprint and chunk
  count before the first run and after the last; a mismatch voids the comparison.
- **Order: Qwen3.6, candidate, candidate, Qwen3.6.** Fresh Qwen3.6 runs bracket the
  candidate, so a drift in corpus, judge or live load shows up as a gap between the two
  Qwen3.6 runs instead of hiding in the comparison. (The Qwen3.8 runs reused Qwen3.6 runs
  from earlier that day and had to list that as a weakness.)
- **Load asymmetry.** Qwen3.6 on `:8001` also serves live chat; the candidate on `:8002`
  has no other traffic. Run overnight, and count live chat turns during each run window
  from Postgres.
- **Identifier probe.** Replay the 2026-10-13 `kempner_rtx` prompt
  (`/scratch/a2rchi/bench-runs/qwen36-name-slip/replay.py`, pointed at `:8002`): 200
  draws, 8 identical requests at once, temperature 0.3. Qwen3.6 gave 0 / 200 wrong.
- **Capacity probe.** 8 bank questions sent at once, 5 rounds, on each server; record p90
  time per question. Production sees several chats at once; a smaller cache pool (§2)
  shows up here, not in the one-at-a-time benchmark.
- **Pre-registration.** Copy the Qwen3.8 PREREGISTRATION.md, fill in §6 as signed off,
  timestamp it, before the first candidate run.

### Phase 3 — adoption (only after a pass and a recorded decision)

1. Commit the launcher and a systemd unit to version control first (#286) — production
   must be restorable from git.
2. Serve the winner on GPUs 2–3 / `:8001` and stop Qwen3.6; or keep it on `:8002`
   and switch `default_provider` — either way, keep Qwen3.6 weights and its unit file
   until one clean week.
3. `dev.yaml`: model id, `models:` list, the `context_windows` entry, sampling (#701);
   redeploy with `deploy/scripts/redeploy.sh` (editing `dev.yaml` and restarting is a
   no-op — config is re-seeded into Postgres at deploy); verify with a live chat turn.
4. Open a new golden-set era in `bench_out/banks/golden/report/goldenset_timeline.yaml`:
   numbers before and after a model change are not comparable history.

## 6. Decision rule (fixed before the first Phase 2 run)

Scored on the 104 bank questions (anchors excluded, as in the Qwen3.8 comparison). Each
question's score is the mean of that model's two runs; differences are paired by
question over questions scored in all four runs.

**Quality — non-inferiority (default).** The candidate passes when, for both
`answer_correctness` and `faithfulness`, the mean paired difference minus 2 standard
errors is above **−0.05**; and, for source accuracy, questions newly answered with the
wrong source minus questions newly fixed is ≤ 3 in each replicate. The 0.05 margin is
the size of the October retrieval fix (+0.053 correctness, #669/#673): a model swap must
not give back what that change bought.

**Quality — superiority (if the motive is better answers).** `compare_runs.py` clears its
gate (effect above 2·SE and 2·σ) on `answer_correctness` or `faithfulness` in both
replicates, and the other metric passes the non-inferiority test above.

**Reported, not gated:** `answer_relevancy`, `context_precision`, `context_recall` (the
model writes the search queries, so retrieval can move), judge cost per run.

**Behaviour — every candidate run must pass every row, under either bar.** References
are the Qwen3.6 runs of 2026-10-12; the bracketing Qwen3.6 runs of this trial replace
them.

| Measure | Qwen3.6 reference | Gate |
|---|---|---|
| Mean seconds per question | 29.9 / 34.6 | ≤ 1.5× the bracketing Qwen3.6 mean |
| p90 seconds per question | 48.2 / 70.8 | ≤ 2× |
| Give-up answers (counted as in `model_subsets.py`) | 4 / 5 | ≤ 8 |
| Malformed search calls (`No query found.`) | 43 / 42 | ≤ 1.25× |
| Degraded answers (context overflow) | 0 / 0 | 0 |
| Identifier probe, wrong spellings | 0 / 200 | ≤ 2 / 200 |
| Capacity probe, p90 at 8 at once | measured in Phase 2 | ≤ 2× |

Qwen3.8 would have failed three of these rows (mean, p90, give-ups) — that is the intent.

## 7. Cost and time (Llama 3.3 70B)

| Phase | GPU / wall time | Judge spend |
|---|---|---|
| 0 | download ~40 GB + ~2 h of checks | — |
| 1 | ~2 h | ~$3 |
| 2 | ingest 0–1.5 h; 2 Qwen3.6 runs ~2 h each; 2 candidate runs est. 4–6 h each (Qwen3.8 took ~3 h); probes ~1 h | ~$28 per Qwen3.6 run, est. $45–60 per candidate run: **~$150–180** |
| 3 | one redeploy (~1 h re-index) | — |

## 8. Ledger

| Candidate | Phase reached | Result | Date | Evidence |
|---|---|---|---|---|
| Llama 3.3 70B Instruct | — | — | — | — |
| Gemma 4 31B IT | blocked before Phase 0 | no model code and no head-size-512 attention kernel in the image | 2026-10-13 | §2 |
| Command R+ 08-2024 | blocked before Phase 0 | no vLLM tool parser for its format; needs all four GPUs and more disk; non-commercial license | 2026-10-13 | §2 |

## 9. Decisions needed from the operator

1. **Bar:** non-inferiority (provenance is the motive) or superiority (§1, §6).
2. **Thresholds** in §6 — confirm or change before pre-registration.
3. **Gemma 4:** fund a separate spike — port the Gemma 4 architecture into the Volta
   fork and extend its attention kernel to head size 512, with its own estimate and the
   rebuild risk of #286 (the image has no build recipe) — covering both 31B and 26B-A4B;
   or drop Gemma 4.
4. **Command R+: recommend dropping.** Trialling it needs all of: a tool parser written
   for its output format, a trial window with production failed over to the Anthropic
   standby (it needs all four GPUs), deleting more of the model cache, and a legal
   reading of CC-BY-NC for institutional use — for a model two generations old.
5. **Qwen3.8:** stop and disable `vllm-qwen38` (needs `sudo`; disable too, or a reboot
   restarts it onto the candidate's GPUs) and delete its 20 GB of weights.

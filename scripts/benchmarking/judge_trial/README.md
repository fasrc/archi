# archi judge trial on Slurm (H200): which open-weights RAGAS judge to adopt

**Goal.** Score the same golden-set answers that Sonnet 4.5 scored in October with each
candidate open-weights judge, on FASRC H200 nodes, and compare row by row
(fasrc/archi#737, step 5.3). The package is self-contained: bundles (full retrieved
passages), the Sonnet reference scores, the unrelated-answer control, the pinned vLLM and
scorer images, the judge definitions and the scripts. Jobs sit in the queue until cards
free up; each job is resumable, so a time-out or a requeue only costs the chunk in flight.

**Where.** `/n/holylabs/jharvard_lab/Everyone/archi_holygpu7c0717/judge-trial` (this
folder). Weights live beside it in `../huggingface` (`hf` here is a link to it). Submit
from a FASRC login node as yourself; the archi service account cannot run `sbatch`.

## What is in the box

| Path | What it is |
|---|---|
| `bundles/<run>-arm0/` | One folder per October run (5 runs, 425 rows): `rows.jsonl` with the question, the **full** retrieved passages, the answer and the reference; `manifest.json`; `BUNDLE_COMPLETE`. Rows were rebuilt from the run's corpus database (0 passage mismatches); 120 of 545 rows whose sources had no section id are absent — their recovery is fasrc/archi#787, not this trial. |
| `bundles/control-unrelated-<run>-arm0/` | 20 rows of one run with the **answer of another question** (distinct donors, no shared passage, seed 20261027). A judge must score these low on faithfulness, answer_relevancy and answer_correctness. |
| `reference/<run>.sonnet.jsonl` | Sonnet 4.5's per-row scores for the five RAGAS metrics (from the October result files), joined by row key. `reference/<run>.meta.json`: the run's judge identity, token usage and ragas settings. `reference/control.jsonl`: each control row's donor and the original row's Sonnet scores. |
| `judges/<judge>.env` | One file per candidate: model id, **pinned revision**, precision, card count, memory, time limit, the vLLM server flags from its model card, reasoning settings, seed. |
| `images/vllm-openai-v0.32.0-cu129.sif` | `vllm/vllm-openai:v0.32.0-cu129` as a SIF, fetched from Docker Hub as an OCI layout by `scripts/oci_fetch.py` (singularity's own pull crawled on this host) and built with `singularity build`. Every scores file records its SHA-256. |
| `images/SHA256SUMS` | Both image digests; `sha256sum -c SHA256SUMS` in `images/` verifies them. The scorer's digest also stands in `scorer-identity.json`. |
| `images/archi-scorer.sif` + `scorer-identity.json` | The archi benchmark image (the code that scores inline, ragas 0.3.5) plus the judge scripts, built by `scripts/build_scorer_sandbox.sh`: the production recipe `scripts/benchmarking/slurm/build_scorer_image.sh` needs `--fakeroot`, which the dev host lacks, so the same steps run as a sandbox build. |
| `scripts/` | `judge_trial.sbatch` (the job), `submit.sh`, `status.sh`, `trial_score.py` (runs inside the scorer image), `compare.py` (the report), `package.py` (how the bundles were built). The same files live in archi under `scripts/benchmarking/judge_trial/`. `oci_fetch.py` and `build_scorer_sandbox.sh` (how the two images were made) live only here, beside the images. |
| `scores/<judge>/` | Written by the jobs: `<bundle>.judge_scores.json` per bundle, and `*.partial.jsonl` checkpoints while a bundle is in progress. |
| `logs/` | Job logs (`archi-judge-trial-<judge>-<jobid>.out`) and vLLM server logs. |
| `bundles-unscored/` | The five 2026-10-29 embedding-trial runs (517 rows) rebuilt the same way. No Sonnet scores exist for them; score them with the **chosen** judge afterwards, not in the trial. |

## The candidates

| judge file | Model | Cards | Why |
|---|---|---|---|
| `gpt-oss-120b` | openai/gpt-oss-120b @ `b5c939de` (MXFP4) | 1 | First pick: unrelated to every answering model and to Lynx; 131k context |
| `nemotron-3-super-fp8` | nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-FP8 @ `744b1880` | 2 | Second judge from another maker; same family as two replacement candidates, so it may favour them |
| `mistral-small-4-119b` | mistralai/Mistral-Small-4-119B-2603 @ `a11f36be` (FP8) | 2 | Reserve; no track record as a RAGAS judge |
| `llama-3.3-70b` | meta-llama/Llama-3.3-70B-Instruct @ `6f6073b4` (bf16) | 2 | Fallback; weaker reasoning; the base of Lynx. **Gated** — step 1b |

Three candidates ship pre-quantized, so their files say `JUDGE_PRECISION=checkpoint`: the
job passes `--dtype auto` and **no** `--quantization` flag (vLLM reads the method from the
checkpoint). Reasoning models are served with their reasoning parser (`nemotron_v3`,
`mistral`; gpt-oss's harmony format is built into vLLM) so the thinking never reaches the
JSON that ragas parses; the effort is set per judge in its env file.

## 1. Before the first submit (once)

**1a. Check the staging.** From a login node:

```bash
cd /n/holylabs/jharvard_lab/Everyone/archi_holygpu7c0717/judge-trial
ls images/            # vllm-openai-v0.32.0-cu129.sif, archi-scorer.sif, scorer-identity.json
ls hf/hub/            # models--openai--gpt-oss-120b, models--nvidia--…, models--mistralai--…, models--sentence-transformers--all-mpnet-base-v2
bash scripts/judge_trial.sbatch --dry-run gpt-oss-120b     # prints the commands, starts nothing
```

The dry run must print one `vllm serve … --uds …` line and one `trial_score.py` line and
exit 0. `all-mpnet-base-v2` is the embedding model ragas needs for answer_relevancy and
answer_correctness; the job runs fully offline (`HF_HUB_OFFLINE=1`).

The package is owned by the archi service account; the jobs run as you, and you need no
group membership. `scores/`, `logs/` and `.cache/` are world-writable with the group
inherited (the same mode as the `Everyone` folder above the package), which is all a job
writes to, plus a socket folder under the node's `$TMPDIR`. Everything else is read-only
for you by design. A permission error from `sbatch` or inside a job therefore points at a
path outside those three folders; report it rather than changing modes on the weights.

**1b. Llama 3.3 70B only (optional).** The weights are gated. With your own Hugging Face
account, accept the license on the model page, then on a node with internet:

```bash
HF_HOME=/n/holylabs/jharvard_lab/Everyone/archi_holygpu7c0717/huggingface \
HF_TOKEN=<your token> hf download meta-llama/Llama-3.3-70B-Instruct \
  --revision 6f6073b423013f6a7d4d9f39144961bfbfbc386b --exclude "original/*"
```

`submit.sh` refuses a judge whose snapshot folder is missing, so nothing breaks if you skip
this; `--all` skips Llama on purpose.

## 2. Submit

```bash
cd /n/holylabs/jharvard_lab/Everyone/archi_holygpu7c0717/judge-trial
bash scripts/submit.sh --all                       # gpt-oss-120b, nemotron-3-super-fp8, mistral-small-4-119b
bash scripts/submit.sh llama-3.3-70b               # after 1b
```

`submit.sh` reads the card count, memory and time limit from the judge file and runs
`sbatch --gres=gpu:N --mem=… -t …` on `scripts/judge_trial.sbatch <judge>`. The default
partition is `gpu_h200`; `--partition gpu_requeue` is the faster-start alternative (jobs are
`--requeue`-safe). One job per judge; the jobs are independent and may run in any order.

What a job does, in order: pins and hashes both images; starts vLLM on a private Unix
socket (no TCP port) with a random per-job key kept in the environment; waits for the
server (a 120 GB model takes 5–20 minutes to load from the share); then, for every bundle
under `bundles/`, runs `trial_score.py` inside the scorer image, which judges each metric
in chunks of 8 rows and appends every (row, metric) score to a checkpoint before moving
on. A bundle whose scores file already exists is skipped. The job log is
`logs/archi-judge-trial-<judge>-<jobid>.out`; the server log is `logs/vllm-<judge>-<jobid>.log`.

Expected scoring time once the server is up: roughly 1–2 hours per judge for the 445
rows × 5 metrics (about 6,900 judge calls; Sonnet needed 1,697 calls for 109 rows). Time
limits are 8–10 h per job to leave room for queue preemption and requeues.

## 3. Watch

```bash
bash scripts/status.sh
```

prints your trial jobs from `squeue`, then for each judge and bundle: `SCORED` with the
aggregates, `partial n/m pairs` from the checkpoint, or `pending`. A job that ended with
a bundle `partial` is resumed by submitting the same judge again — `submit.sh <judge>` —
and continues from the checkpoint.

If a job fails before scoring, read the vLLM log first: a wrong flag for the image, an
out-of-memory at start, or a model needing a newer vLLM shows there. Fix the judge file
(`judges/<judge>.env`) and resubmit. The dry run checks the file's syntax without a GPU.

## 4. Compare (on the dev host, or any machine with Python 3.10+)

```bash
cd /n/holylabs/jharvard_lab/Everyone/archi_holygpu7c0717/judge-trial
python3 scripts/compare.py --package . --out report.md --json report.json
```

For each judge and metric over the real bundles: rows paired, Spearman and Pearson
correlation with Sonnet, both means, mean difference, mean absolute difference, agreement
at the 0.5 cut, and how many rows each judge left unscored (parse failures and time-outs
count here — they are a primary result, not a footnote). Then the control: the judge's
mean and the share below 0.5 on the swapped answers, beside Sonnet's mean on the
originals. Then throughput and tokens per judge. Standard library only.

How to read it: a usable judge ranks rows like Sonnet (Spearman well above 0.5 on
faithfulness and answer_correctness), fails the swapped answers (control means near 0,
share below 0.5 near 1), and leaves few rows unscored. A level shift (judge mean ≠ Sonnet
mean) is expected and acceptable: a new judge starts a **new era** in the golden-set
report and is never compared with Sonnet-scored history.

## 5. Record

Copy `report.md`, `report.json` and `scores/` into `fasrc/archi-bench-out` under
`checks/2026-10-judge-trial/`, commit, and record the choice on fasrc/archi#737 (step 5.3)
with the numbers. The adopted judge then goes into `judge.env` of the production offload
(`docs/docs/benchmarking.md`, "Offloaded RAGAS judge on Slurm"), with its precision value
from the trial file.

## Result of the first run (2026-10-30/31)

gpt-oss-120b (1 card, 174 min): 0 of 2125 pairs unscored, Spearman 0.66–0.83 against Sonnet, every
swapped answer rejected. Nemotron 3 Super (thinking on, 2 cards, 332 min): agreement as good or better
where it scores, but 119 pairs unscored (14 % of faithfulness rows). Mistral Small 4 (reasoning off,
2 cards, 75 min): lenient, below 0.5 rank agreement on three metrics. Verdict: gpt-oss-120b. The
report is `report.md` in the package and `checks/2026-10-judge-trial/` in archi-bench-out.

## Notes and limits

- The trial scores only the 425 rebuilt rows; the 120 parent-less rows are a known gap.
- Bundles record `judge_mode: inline+bundle` because the rows were judged inline (Sonnet)
  and bundled; `publish.py` never queues such bundles into the production queue.
- The scorer image was built from `benchmarking-embtrial-a:2000` (archi dev tip at
  2026-10-28) by a sandbox build, not by `build_scorer_image.sh`. Its
  `scorer-identity.json` names the code and package digests; they are copied into every
  scores file.
- The 46 human-graded records and the planted edits of the free-faithfulness-judge
  pre-registration do not exist yet (fasrc/archi#786 material lock pending); that
  calibration check is deferred and is not part of this package.
- Nothing in the job needs a secret: no OpenAI embeddings, no gated download at run time.
- The vLLM image `v0.32.0-cu129` ships `torchcodec` 0.17.0 built for CUDA 13; its import
  fails with an `OSError` for `libnvrtc.so.13`, which vLLM does not catch, so the server
  dies at start. The job binds an empty folder over the `torchcodec` package, vLLM then
  runs with audio decoding off (irrelevant for a text judge). `TRIAL_HIDE_TORCHCODEC_AT=`
  (empty) disables the workaround for an image that does not need it.
- Compute nodes refuse a symlink as a bind source, so the job binds the resolved weights
  folder, not `hf`; and pins the images under `.cache/pins/` because `images/` is read-only
  for the operator. Both bit the first submissions (2026-10-30).
- `.cache/` keeps the inputs the two images were built from — the OCI layout of
  `vllm/vllm-openai:v0.32.0-cu129` (11 GB) and the docker archive of
  `benchmarking-embtrial-a:2000` (5 GB) — plus the dev-host smoke bundle and its scores.
  They are provenance, not used by the jobs; delete them once the images are verified
  against `images/SHA256SUMS`.

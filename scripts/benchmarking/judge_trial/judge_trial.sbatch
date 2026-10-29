#!/usr/bin/env bash
#SBATCH -J archi-judge-trial
#SBATCH -p gpu_h200
#SBATCH --gres=gpu:1
#SBATCH -c 16
#SBATCH --mem=200G
#SBATCH -t 08:00:00
#SBATCH --requeue
#SBATCH --open-mode=append
#SBATCH -o logs/archi-judge-trial-%j.out
#
# The judge trial job (fasrc/archi#737, step 5.3): serve ONE candidate judge with vLLM on
# a private Unix socket and score EVERY bundle of the package with it, through the same
# ragas code the inline benchmark uses (trial_score.py in the scorer image).
#
#   cd <package root>
#   bash scripts/judge_trial.sbatch --dry-run <judge>          # print the commands only
#   bash scripts/submit.sh <judge>                              # sbatch with the judge's sizes
#
# <judge> names judges/<judge>.env, which pins the model id, the weight revision, the
# precision, the card count and the server flags. The header above is the one-card
# default; submit.sh passes --gres, --mem and -t from the env file. Slurm runs a COPY of
# this file, so the package root is TRIAL_HOME or $SLURM_SUBMIT_DIR: submit from the root.
#
# Order of work: pin the images and hash them; export a random per-job VLLM_API_KEY into
# the containers' environment (never on a command line); start vLLM with --uds and no TCP
# listener; wait for GET /v1/models = 200; run trial_score.py once per bundle folder (a
# bundle with a finished scores file is skipped, a half-scored one resumes from its
# checkpoint); stop vLLM. --requeue is safe for the same reason. Scores, checkpoints and
# logs are written under the package root with umask 022, so the whole lab group can read
# them (nothing here is secret).
set -euo pipefail
umask 022

die() {
  echo "judge_trial: refused: $*" >&2
  exit 1
}
log() { echo "judge_trial: $(date -u +%Y-%m-%dT%H:%M:%SZ) $*" >&2; }

DRY_RUN=false
if [ "${1:-}" = --dry-run ]; then
  DRY_RUN=true
  shift
fi
[ $# -eq 1 ] || die "usage: sbatch scripts/judge_trial.sbatch <judge> | bash scripts/judge_trial.sbatch --dry-run <judge>"
JUDGE="$1"
case "$JUDGE" in
  */* | .* | "") die "<judge> is a name under judges/, not a path" ;;
esac

# --- Settings ---------------------------------------------------------------------------
if [ -n "${SLURM_JOB_ID:-}" ]; then
  PKG="${TRIAL_HOME:-${SLURM_SUBMIT_DIR:-$PWD}}"
else
  PKG="${TRIAL_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)}"
fi
[ -f "$PKG/scripts/trial_score.py" ] || die "$PKG is not the package root (no scripts/trial_score.py); submit from the package root or set TRIAL_HOME"
JUDGE_ENV="$PKG/judges/$JUDGE.env"
VLLM_IMAGE="${TRIAL_VLLM_IMAGE:-$PKG/images/vllm-openai-v0.32.0-cu129.sif}"
SCORER_IMAGE="${TRIAL_SCORER_IMAGE:-$PKG/images/archi-scorer.sif}"
# The Hugging Face cache that holds every judge's weights at its pinned revision, and the
# ragas embedding model (sentence-transformers/all-mpnet-base-v2). Offline in the job.
HF_HOME_DIR="${TRIAL_HF_HOME:-$PKG/hf}"
CACHE_DIR="$PKG/.cache"
OUT_DIR="$PKG/scores/$JUDGE"
BUNDLES_DIR="$PKG/bundles"
METRICS="${TRIAL_METRICS:-manifest}"
CHUNK_SIZE="${TRIAL_CHUNK_SIZE:-8}"
READY_TIMEOUT_SECONDS="${TRIAL_READY_TIMEOUT:-3600}"
READY_POLL_SECONDS="${TRIAL_READY_POLL_SECONDS:-15}"
# Inside the scorer image, archi lives at /root/archi (archi-scorer.def).
ARCHI_ROOT=/root/archi
# ----------------------------------------------------------------------------------------

RUNTIME="${TRIAL_RUNTIME:-}"
if [ -z "$RUNTIME" ]; then
  RUNTIME="$(command -v apptainer || command -v singularity || true)"
fi
JOB_ID="${SLURM_JOB_ID:-<job id>}"
GPUS="${SLURM_GPUS_ON_NODE:-1}"

# The judge file as shell assignments. Same rules as the production judge.env parser
# (judge_queue.read_judge_env): KEY=VALUE lines, a comment on its own line, matching outer
# quotes stripped. Standard library only, so it runs on any login or compute node.
judge_settings() {
  python3 - "$JUDGE_ENV" <<'PY'
import shlex, sys
values = {}
for number, raw in enumerate(open(sys.argv[1]).read().splitlines(), 1):
    line = raw.strip()
    if not line or line.startswith("#"):
        continue
    if "=" not in line:
        sys.exit(f"{sys.argv[1]}: line {number} is not KEY=VALUE: {raw!r}")
    key, value = (part.strip() for part in line.split("=", 1))
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    values[key] = value
for key in ("JUDGE_MODEL_ID", "JUDGE_MODEL_REVISION", "JUDGE_PRECISION"):
    if not values.get(key):
        sys.exit(f"{sys.argv[1]}: {key} is missing or empty")
for name, key in (("MODEL_ID", "JUDGE_MODEL_ID"), ("MODEL_REVISION", "JUDGE_MODEL_REVISION"),
                  ("PRECISION", "JUDGE_PRECISION"), ("WANT_GPUS", "JUDGE_GPUS")):
    print(f"{name}={shlex.quote(values.get(key) or '')}")
args = shlex.split(values.get("JUDGE_SERVER_ARGS") or "")
print("SERVER_ARGS=(" + " ".join(shlex.quote(a) for a in args) + ")")
PY
}

[ -f "$JUDGE_ENV" ] || die "no judge file at $JUDGE_ENV"
SETTINGS="$(judge_settings)" || die "$JUDGE_ENV does not load (see above)"
eval "$SETTINGS"
[ -n "$MODEL_ID" ] && [ -n "$MODEL_REVISION" ] || die "$JUDGE_ENV must set JUDGE_MODEL_ID and JUDGE_MODEL_REVISION"
case "$PRECISION" in
  bf16) PRECISION_ARGS=(--dtype bfloat16) ;;
  fp16) PRECISION_ARGS=(--dtype float16) ;;
  fp8) PRECISION_ARGS=(--quantization fp8) ;;
  # Pre-quantized weights (MXFP4, modelopt FP8, FP8 checkpoints): vLLM reads the method
  # from the checkpoint's config.json, and an explicit --quantization would be wrong.
  checkpoint) PRECISION_ARGS=(--dtype auto) ;;
  *) die "JUDGE_PRECISION=$PRECISION has no vLLM setting here; use bf16, fp16, fp8 or checkpoint" ;;
esac
for arg in ${SERVER_ARGS[@]+"${SERVER_ARGS[@]}"}; do
  case "${arg%%=*}" in
    --model | --revision | --served-model-name | --dtype | --quantization | -q \
      | --tensor-parallel-size | -tp | --uds | --host | --port | --api-key)
      die "JUDGE_SERVER_ARGS sets ${arg%%=*}, which the job sets itself or must not set"
      ;;
  esac
done
if [ -n "$WANT_GPUS" ] && [ -n "${SLURM_JOB_ID:-}" ] && [ "$WANT_GPUS" != "$GPUS" ]; then
  die "$JUDGE_ENV asks for JUDGE_GPUS=$WANT_GPUS but the job has $GPUS GPU(s); submit with scripts/submit.sh"
fi
# Outside Slurm (the dry run) the judge file's card count stands in for the allocation.
[ -n "${SLURM_GPUS_ON_NODE:-}" ] || GPUS="${WANT_GPUS:-$GPUS}"

SOCK_DIR=""
VLLM_PID=""
PINNED_VLLM=""
PINNED_SCORER=""
cleanup() {
  if [ -n "$VLLM_PID" ]; then
    kill "$VLLM_PID" 2>/dev/null || true
    wait "$VLLM_PID" 2>/dev/null || true
  fi
  [ -z "$SOCK_DIR" ] || rm -rf "$SOCK_DIR"
  [ -z "$PINNED_VLLM" ] || rm -f "$PINNED_VLLM"
  [ -z "$PINNED_SCORER" ] || rm -f "$PINNED_SCORER"
}

if [ "$DRY_RUN" = true ]; then
  SOCK_DIR="${TMPDIR:-/tmp}/archi-judge-trial.$JOB_ID.XXXXXX"
  SERVER_IMAGE_DIGEST="sha256:<sha256 of $VLLM_IMAGE>"
  SCORER_IMAGE_DIGEST="sha256:<sha256 of $SCORER_IMAGE>"
  PINNED_VLLM="$VLLM_IMAGE"
  PINNED_SCORER="$SCORER_IMAGE"
else
  [ -n "${SLURM_JOB_ID:-}" ] || die "not in a Slurm job; submit it with sbatch, or use --dry-run"
  [ -n "$RUNTIME" ] || die "neither apptainer nor singularity is on PATH"
  [ -f "$VLLM_IMAGE" ] || die "no vLLM image at $VLLM_IMAGE"
  [ -f "$SCORER_IMAGE" ] || die "no scorer image at $SCORER_IMAGE"
  [ -d "$HF_HOME_DIR/hub" ] || die "no Hugging Face cache at $HF_HOME_DIR/hub"
  [ -d "$BUNDLES_DIR" ] || die "no bundles folder at $BUNDLES_DIR"
  mkdir -p "$OUT_DIR" "$PKG/logs" "$CACHE_DIR/apptainer" "$CACHE_DIR/xdg"
  SOCK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/archi-judge-trial.$SLURM_JOB_ID.XXXXXX")"
  trap cleanup EXIT
  chmod 700 "$SOCK_DIR"
  [ "${#SOCK_DIR}" -le 90 ] || die "the socket folder $SOCK_DIR is too long for a Unix socket path; set TMPDIR shorter"
  # Pin both images for the whole job (a hard link keeps one inode even if the file is
  # replaced), and record what was served: the digests go into every scores file.
  PINNED_VLLM="$(dirname "$VLLM_IMAGE")/.vllm.$SLURM_JOB_ID.sif"
  PINNED_SCORER="$(dirname "$SCORER_IMAGE")/.scorer.$SLURM_JOB_ID.sif"
  ln "$VLLM_IMAGE" "$PINNED_VLLM" 2>/dev/null || cp "$VLLM_IMAGE" "$PINNED_VLLM"
  ln "$SCORER_IMAGE" "$PINNED_SCORER" 2>/dev/null || cp "$SCORER_IMAGE" "$PINNED_SCORER"
  SERVER_IMAGE_DIGEST="sha256:$(sha256sum "$PINNED_VLLM" | cut -d' ' -f1)"
  SCORER_IMAGE_DIGEST="sha256:$(sha256sum "$PINNED_SCORER" | cut -d' ' -f1)"
  export APPTAINER_CACHEDIR="$CACHE_DIR/apptainer" SINGULARITY_CACHEDIR="$CACHE_DIR/apptainer"
  KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
  # The key reaches both containers through the environment only. Apptainer and
  # Singularity read different prefixes; set both.
  export APPTAINERENV_VLLM_API_KEY="$KEY" SINGULARITYENV_VLLM_API_KEY="$KEY"
  printf 'Authorization: Bearer %s\n' "$KEY" > "$SOCK_DIR/auth.header"
  chmod 600 "$SOCK_DIR/auth.header"
fi

SOCK="$SOCK_DIR/vllm.sock"
SERVE=("$RUNTIME" exec --nv --cleanenv
  --bind "$SOCK_DIR" --bind "$HF_HOME_DIR" --bind "$CACHE_DIR"
  --env "HF_HOME=$HF_HOME_DIR" --env HF_HUB_OFFLINE=1 --env TRANSFORMERS_OFFLINE=1
  --env "XDG_CACHE_HOME=$CACHE_DIR/xdg" --env "VLLM_CACHE_ROOT=$CACHE_DIR/vllm"
  "$PINNED_VLLM"
  vllm serve "$MODEL_ID" --revision "$MODEL_REVISION" --served-model-name "$MODEL_ID"
  --tensor-parallel-size "$GPUS" "${PRECISION_ARGS[@]}" ${SERVER_ARGS[@]+"${SERVER_ARGS[@]}"}
  --uds "$SOCK")
PROBE=(curl -sS -o /dev/null -w '%{http_code}' --unix-socket "$SOCK"
  -H "@$SOCK_DIR/auth.header" http://localhost/v1/models)
score_cmd() { # $1 = bundle folder
  SCORE=("$RUNTIME" exec --cleanenv
    --bind "$PKG" --bind "$SOCK_DIR" --bind "$HF_HOME_DIR"
    --env "HF_HOME=$HF_HOME_DIR" --env HF_HUB_OFFLINE=1 --env TRANSFORMERS_OFFLINE=1
    --env "ARCHI_ROOT=$ARCHI_ROOT" --env "PYTHONPATH=$ARCHI_ROOT"
    --env "SLURM_JOB_ID=${SLURM_JOB_ID:-}" --env "SLURM_RESTART_COUNT=${SLURM_RESTART_COUNT:-0}"
    "$PINNED_SCORER"
    python3 "$PKG/scripts/trial_score.py"
    --bundle "$1" --out-dir "$OUT_DIR" --judge-env "$JUDGE_ENV" --socket "$SOCK"
    --metrics "$METRICS" --chunk-size "$CHUNK_SIZE"
    --server-image-digest "$SERVER_IMAGE_DIGEST" --scorer-image-digest "$SCORER_IMAGE_DIGEST"
    --job-id "$JOB_ID")
}

show() {
  printf '%q' "$1"
  shift
  printf ' %q' "$@"
  printf '\n'
}

if [ "$DRY_RUN" = true ]; then
  echo "# judge $JUDGE: $MODEL_ID@$MODEL_REVISION, precision $PRECISION, $GPUS GPU(s)"
  echo "# 1. pin and hash $VLLM_IMAGE and $SCORER_IMAGE; export VLLM_API_KEY=<random> into the containers"
  echo "# 2. start vLLM on the private socket"
  show "${SERVE[@]}"
  echo "# 3. wait for HTTP 200 (up to ${READY_TIMEOUT_SECONDS}s)"
  show "${PROBE[@]}"
  echo "# 4. score each bundle under $BUNDLES_DIR into $OUT_DIR"
  score_cmd "$BUNDLES_DIR/<bundle>"
  show "${SCORE[@]}"
  exit 0
fi

log "job $SLURM_JOB_ID on $(hostname): judge $JUDGE = $MODEL_ID@$MODEL_REVISION ($PRECISION, $GPUS GPU)"
log "vLLM image $SERVER_IMAGE_DIGEST; scorer image $SCORER_IMAGE_DIGEST"
"${SERVE[@]}" > "$PKG/logs/vllm-$JUDGE-$SLURM_JOB_ID.log" 2>&1 &
VLLM_PID=$!

waited=0
until [ "$("${PROBE[@]}" 2>/dev/null || true)" = 200 ]; do
  if ! kill -0 "$VLLM_PID" 2>/dev/null; then
    die "vLLM exited before it was ready; see $PKG/logs/vllm-$JUDGE-$SLURM_JOB_ID.log"
  fi
  if [ "$waited" -ge "$READY_TIMEOUT_SECONDS" ]; then
    die "vLLM was not ready after ${READY_TIMEOUT_SECONDS}s; see $PKG/logs/vllm-$JUDGE-$SLURM_JOB_ID.log"
  fi
  sleep "$READY_POLL_SECONDS"
  waited=$((waited + READY_POLL_SECONDS))
done
log "vLLM ready after ${waited}s"

status=0
for bundle in "$BUNDLES_DIR"/*/; do
  bundle="${bundle%/}"
  [ -f "$bundle/BUNDLE_COMPLETE" ] || { log "skip $bundle: no BUNDLE_COMPLETE"; continue; }
  if [ -f "$OUT_DIR/$(basename "$bundle").judge_scores.json" ]; then
    log "skip $(basename "$bundle"): already scored"
    continue
  fi
  log "scoring $(basename "$bundle")"
  score_cmd "$bundle"
  if ! "${SCORE[@]}"; then
    log "FAILED $(basename "$bundle") (see above); continuing with the next bundle"
    status=1
  fi
done
log "done (status $status)"
exit "$status"

#!/usr/bin/env bash
#SBATCH -J archi-judge
#SBATCH -p gpu_h200
#SBATCH --gres=gpu:2
#SBATCH -c 16
#SBATCH --mem=256G
#SBATCH -t 1-00:00:00
#SBATCH --requeue
#SBATCH --open-mode=append
#SBATCH -o archi-judge-%j.out
#
# The partition, GPU count and time limit above are the design's first option: gpu_h200,
# 2 x H200 in bf16. The operator confirms them on fasrc/archi#729. For 1 x H200 in fp8, use
# --gres=gpu:1 and JUDGE_PRECISION=fp8 in judge.env (precision is part of the judge identity).

# The offloaded RAGAS judge job (#733; design D5, D6 and D10 in
# openspec/changes/archive/*-offload-ragas-judge-to-slurm).
#
#   sbatch --dependency=singleton scripts/benchmarking/slurm/archi_judge.sbatch
#   bash scripts/benchmarking/slurm/archi_judge.sbatch --dry-run
#
# judge_submit.sh submits it from the repo root (Slurm runs a copy of this file, so the repo
# is ARCHI_REPO or $SLURM_SUBMIT_DIR). In order, the job:
#   1. checks its live scorer identity against scorer-identity.json and counts its work, in
#      the scorer image, before any model loads. No work: it exits 0. A stale sidecar: the
#      check writes <queue>/SCORER_STALE.<token> and the job exits non-zero;
#   2. starts vLLM in Apptainer on a Unix socket in a 0700 folder under the job's temporary
#      folder, with no TCP listener. A random per-job key goes to VLLM_API_KEY through the
#      environment (APPTAINERENV_VLLM_API_KEY), never on a command line, which other users
#      can read through ps;
#   3. waits until GET /v1/models through the socket returns HTTP 200;
#   4. runs the scorer (score.py run), which claims and scores the matching bundles;
#   5. submits archi-judge-merge with --dependency=afterok on this job.
# A trap stops vLLM and removes the socket folder on every exit. --requeue is safe: a
# requeued job keeps its id, so it resumes its own claims from their checkpoints (D5a, D7).
# --dry-run prints the resolved commands and starts nothing.
set -euo pipefail
umask 077

# --- Settings (the operator confirms them on fasrc/archi#729) -------------------------
JUDGE_HOME="${JUDGE_HOME:-$HOME/archi-judge}"
QUEUE="${JUDGE_QUEUE:-$JUDGE_HOME/queue}"
JUDGE_ENV="${JUDGE_ENV:-$JUDGE_HOME/judge.env}"
SCORER_IMAGE="${JUDGE_SCORER_IMAGE:-$JUDGE_HOME/archi-scorer.sif}"
SIDECAR="${JUDGE_SIDECAR:-$JUDGE_HOME/scorer-identity.json}"
VLLM_IMAGE="${JUDGE_VLLM_IMAGE:-docker://vllm/vllm-openai:v0.31.0-cu129}"
# The judge weights (about 140 GB), the image cache and the vLLM compile cache go to lab
# storage: set JUDGE_LAB (your lab folder under /n/netscratch) or JUDGE_SCRATCH.
LAB="${JUDGE_LAB:-}"
SCRATCH_DIR="${JUDGE_SCRATCH:-${SCRATCH:-/n/netscratch}/${LAB}/Lab/${USER}/archi-judge}"
# Python 3.8 or later on the node, for judge.env (standard library only).
JUDGE_PYTHON="${JUDGE_PYTHON:-python3}"
READY_TIMEOUT_SECONDS="${JUDGE_READY_TIMEOUT:-1800}"
READY_POLL_SECONDS="${JUDGE_READY_POLL_SECONDS:-10}"
# ---------------------------------------------------------------------------------------

die() {
  echo "archi_judge: refused: $*" >&2
  exit 1
}
log() { echo "archi_judge: $*" >&2; }

DRY_RUN=false
if [ "${1:-}" = --dry-run ]; then
  DRY_RUN=true
  shift
fi
[ $# -eq 0 ] || die "usage: sbatch archi_judge.sbatch | bash archi_judge.sbatch --dry-run"

if [ -n "${SLURM_JOB_ID:-}" ]; then
  REPO="${ARCHI_REPO:-${SLURM_SUBMIT_DIR:-$PWD}}"
else
  REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd -P)"
fi
[ -f "$REPO/scripts/benchmarking/judge/judge_queue.py" ] \
  || die "$REPO is not the archi repo root; submit the job from the repo root (judge_submit.sh does)"
JOB_ID="${SLURM_JOB_ID:-<job id>}"
GPUS="${SLURM_GPUS_ON_NODE:-2}"
# The scripts inside the scorer image (archi-scorer.def puts them there).
SCORER_SCRIPTS=/root/archi/scripts/benchmarking/judge

# judge.env, read by the rules of judge_queue.read_judge_env, as shell assignments.
judge_settings() {
  "$JUDGE_PYTHON" - "$REPO/scripts/benchmarking/judge/judge_queue.py" "$JUDGE_ENV" <<'PY'
import importlib.util, shlex, sys
spec = importlib.util.spec_from_file_location("judge_queue", sys.argv[1])
queue = importlib.util.module_from_spec(spec)
spec.loader.exec_module(queue)
env = queue.read_judge_env(sys.argv[2])
for name, key in (("MODEL_ID", "JUDGE_MODEL_ID"), ("MODEL_REVISION", "JUDGE_MODEL_REVISION"),
                  ("PRECISION", "JUDGE_PRECISION")):
    print(f"{name}={shlex.quote(env[key])}")
args = shlex.split(env["JUDGE_SERVER_ARGS"] or "")
print("SERVER_ARGS=(" + " ".join(shlex.quote(a) for a in args) + ")")
PY
}

if [ -f "$JUDGE_ENV" ]; then
  SETTINGS="$(judge_settings)" || die "$JUDGE_ENV does not load (see above)"
  eval "$SETTINGS"
else
  [ "$DRY_RUN" = true ] || die "no judge.env at $JUDGE_ENV"
  log "no judge.env at $JUDGE_ENV; the dry run shows its values as placeholders"
  MODEL_ID="<JUDGE_MODEL_ID>" MODEL_REVISION="<JUDGE_MODEL_REVISION>" PRECISION=bf16
  SERVER_ARGS=()
fi
# The weight precision decides the vLLM setting; the judge identity records the precision.
case "$PRECISION" in
  bf16) PRECISION_ARGS=(--dtype bfloat16) ;;
  fp16) PRECISION_ARGS=(--dtype float16) ;;
  fp8) PRECISION_ARGS=(--quantization fp8) ;;
  *) die "JUDGE_PRECISION=$PRECISION has no vLLM setting here; use bf16, fp16 or fp8" ;;
esac

if [ -z "${JUDGE_SCRATCH:-}" ] && [ -z "$LAB" ]; then
  [ "$DRY_RUN" = true ] || die "set JUDGE_LAB (your lab folder under /n/netscratch) or JUDGE_SCRATCH"
  log "JUDGE_LAB is not set; a job refuses to start without it or JUDGE_SCRATCH"
fi

# The socket folder: private to this user and job. A Unix socket path has at most 107 bytes.
if [ "$DRY_RUN" = true ]; then
  SOCK_DIR="${TMPDIR:-/tmp}/archi-judge.$JOB_ID.XXXXXX"
else
  [ -n "${SLURM_JOB_ID:-}" ] || die "not in a Slurm job; submit it with sbatch, or use --dry-run"
  SOCK_DIR=""
fi

# Every folder a container reads or writes is bound once.
BINDS=()
add_bind() {
  local dir
  for dir in "$@"; do
    case "$dir" in
      *:* | *,*) die "$dir has a ':' or a ','; apptainer --bind cannot take it" ;;
    esac
    [[ " ${BINDS[*]-} " == *" --bind $dir "* ]] || BINDS+=(--bind "$dir")
  done
}

commands() {
  local sock="$SOCK_DIR/vllm.sock"
  BINDS=()
  add_bind "$QUEUE" "$(dirname "$JUDGE_ENV")" "$(dirname "$SIDECAR")"
  CHECK=(apptainer exec --cleanenv "${BINDS[@]}" "$SCORER_IMAGE"
    python3 "$SCORER_SCRIPTS/score.py" check
    --queue "$QUEUE" --judge-env "$JUDGE_ENV" --sidecar "$SIDECAR" --job-id "$JOB_ID")
  add_bind "$SOCK_DIR"
  RUN=(apptainer exec --cleanenv "${BINDS[@]}" "$SCORER_IMAGE"
    python3 "$SCORER_SCRIPTS/score.py" run
    --queue "$QUEUE" --judge-env "$JUDGE_ENV" --sidecar "$SIDECAR" --socket "$sock"
    --job-id "$JOB_ID")
  SERVE=(apptainer exec --nv --cleanenv --bind "$SOCK_DIR" --bind "$SCRATCH_DIR"
    --env "HF_HOME=$SCRATCH_DIR/hf" --env HF_HUB_OFFLINE=1
    --env "XDG_CACHE_HOME=$SCRATCH_DIR/cache"
    "$VLLM_IMAGE"
    vllm serve "$MODEL_ID" --revision "$MODEL_REVISION" --served-model-name "$MODEL_ID"
    --tensor-parallel-size "$GPUS" "${PRECISION_ARGS[@]}" ${SERVER_ARGS[@]+"${SERVER_ARGS[@]}"}
    --uds "$sock")
  PROBE=(curl -sS -o /dev/null -w '%{http_code}' --unix-socket "$sock"
    -H "@$SOCK_DIR/auth.header" http://localhost/v1/models)
  MERGE=(sbatch --parsable "--dependency=afterok:$JOB_ID"
    "$REPO/scripts/benchmarking/slurm/judge_merge.sbatch")
}

show() {
  printf '%q' "$1"
  shift
  printf ' %q' "$@"
  printf '\n'
}

if [ "$DRY_RUN" = true ]; then
  commands
  echo "# 1. check the scorer identity and count the work (exit 3: no work, 2: stale sidecar)"
  show "${CHECK[@]}"
  echo "# 2. export APPTAINERENV_VLLM_API_KEY=<random per job>, then start vLLM"
  show "${SERVE[@]}"
  echo "# 3. wait for HTTP 200, the key in a 0600 header file"
  show "${PROBE[@]}"
  echo "# 4. score"
  show "${RUN[@]}"
  echo "# 5. merge after this job"
  show "${MERGE[@]}"
  exit 0
fi

command -v apptainer >/dev/null || die "apptainer is not on PATH"
[ -f "$SCORER_IMAGE" ] || die "no scorer image at $SCORER_IMAGE; build it with build_scorer_image.sh"
mkdir -p "$SCRATCH_DIR/hf" "$SCRATCH_DIR/cache" "$SCRATCH_DIR/apptainer"
export APPTAINER_CACHEDIR="$SCRATCH_DIR/apptainer"

SOCK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/archi-judge.$SLURM_JOB_ID.XXXXXX")"
VLLM_PID=""
cleanup() {
  if [ -n "$VLLM_PID" ]; then
    kill "$VLLM_PID" 2>/dev/null || true
    wait "$VLLM_PID" 2>/dev/null || true
  fi
  rm -rf "$SOCK_DIR"
}
trap cleanup EXIT
chmod 700 "$SOCK_DIR"
[ "${#SOCK_DIR}" -le 96 ] || die "the socket folder $SOCK_DIR is too long for a Unix socket path; set TMPDIR to a shorter folder"
commands

log "job $SLURM_JOB_ID: checking the scorer identity and the queue $QUEUE"
set +e
"${CHECK[@]}"
CHECK_RC=$?
set -e
case "$CHECK_RC" in
  0) ;;
  3)
    log "no work for this job; vLLM not started"
    exit 0
    ;;
  2) die "the scorer sidecar $SIDECAR does not describe the live scorer; the check wrote SCORER_STALE in $QUEUE; rebuild the scorer image (build_scorer_image.sh)" ;;
  *) die "the scorer check failed with exit $CHECK_RC" ;;
esac

KEY="$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')"
[ "${#KEY}" -eq 64 ] || die "could not make a per-job API key"
export APPTAINERENV_VLLM_API_KEY="$KEY"
printf 'Authorization: Bearer %s\n' "$KEY" > "$SOCK_DIR/auth.header"

log "starting vLLM ($MODEL_ID@$MODEL_REVISION, $PRECISION, $GPUS GPUs) on $SOCK_DIR/vllm.sock"
"${SERVE[@]}" &
VLLM_PID=$!

waited=0
CODE=""
while :; do
  kill -0 "$VLLM_PID" 2>/dev/null || die "vLLM stopped before it was ready (see the job log)"
  CODE="$("${PROBE[@]}" 2>/dev/null || true)"
  [ "$CODE" = 200 ] && break
  [ "$waited" -lt "$READY_TIMEOUT_SECONDS" ] || die "vLLM was not ready after $READY_TIMEOUT_SECONDS seconds (last answer: ${CODE:-none})"
  sleep "$READY_POLL_SECONDS"
  waited=$((waited + READY_POLL_SECONDS))
  [ "$READY_POLL_SECONDS" -gt 0 ] || waited=$((waited + 1))
done
log "vLLM is ready; scoring"

"${RUN[@]}"

log "submitting the merge after job $SLURM_JOB_ID"
"${MERGE[@]}"

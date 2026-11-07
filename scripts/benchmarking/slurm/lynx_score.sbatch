#!/usr/bin/env bash
#SBATCH -J archi-lynx
#SBATCH -p gpu
#SBATCH --gres=gpu:1
#SBATCH -c 4
#SBATCH --mem=64G
#SBATCH -t 02:00:00
#SBATCH --no-requeue
#SBATCH -o archi-lynx-%j.out
#
# Lynx 70B variant: two H200 GPUs. Use these lines in place of the -p, --gres, --mem
# and -t lines above, and the 70B MODEL_ID and MODEL_REVISION below.
# NOTE: the variant cannot load the model yet. The scorer's vLLM backend puts the whole
# model on one GPU (no tensor_parallel_size), and the 70B weights are 141 GB (#755).
##SBATCH -p gpu_h200
##SBATCH --gres=gpu:2
##SBATCH --mem=256G
##SBATCH -t 04:00:00

# Score one run's judge bundle with PatronusAI Lynx, in a pinned vLLM container
# (#735; design D11 in openspec/changes/archive/2026-10-23-add-lynx-faithfulness-scorer).
#
#   sbatch scripts/benchmarking/slurm/lynx_score.sbatch <bundle folder> [<out dir>]
#   bash scripts/benchmarking/slurm/lynx_score.sbatch --dry-run <bundle folder> [<out dir>]
#
# Submit from the repo root: in a job, Slurm runs a copy of this file, so the repo is
# $SLURM_SUBMIT_DIR. The bundle folder is <out_dir>/<judge_bundle_dir>/<basename>-arm<N>/
# of a judge_mode: deferred run, with BUNDLE_COMPLETE. The out dir is the folder of the
# result JSON, where compare_runs.py looks for <basename>-arm<N>.lynx.json. Without the
# second argument it is the nearest folder above the bundle folder that holds
# <basename>.json. --dry-run prints the container command and starts nothing.
#
# Set LYNX_LAB to your lab folder under $SCRATCH (/n/netscratch), or LYNX_SCRATCH to a
# folder of your own. The model, the image cache and the vLLM compile cache (which
# follows XDG_CACHE_HOME) go there, not to the home folder that Apptainer binds in.
# The scorer cannot resume a half-scored bundle, so the job never runs on a partition
# that preempts and requeues it.
set -euo pipefail

# --- Settings ---------------------------------------------------------------
IMAGE="docker://vllm/vllm-openai:v0.31.0-cu129"
MODEL_ID="PatronusAI/Llama-3-Patronus-Lynx-8B-Instruct"
MODEL_REVISION="5523f869eb5eecb4bca417b6e6d3532e746a9664"
# Lynx 70B (see the NOTE at the top):
# MODEL_ID="PatronusAI/Llama-3-Patronus-Lynx-70B-Instruct"
# MODEL_REVISION="503b9b9469aec7be01144517e697a20bd966f27a"
LAB="${LYNX_LAB:-}"
SCRATCH_DIR="${LYNX_SCRATCH:-${SCRATCH:-/n/netscratch}/${LAB}/Lab/${USER}/archi-lynx}"
# -----------------------------------------------------------------------------

die() {
  echo "lynx_score: refused: $*" >&2
  exit 1
}

usage() {
  echo "usage: sbatch lynx_score.sbatch <bundle folder> [<out dir>]" >&2
  echo "       bash lynx_score.sbatch --dry-run <bundle folder> [<out dir>]" >&2
  exit 2
}

DRY_RUN=false
if [ "${1:-}" = --dry-run ]; then
  DRY_RUN=true
  shift
fi
[ $# -ge 1 ] && [ $# -le 2 ] || usage

[ -d "$1" ] || die "$1 is not a folder"
BUNDLE="$(cd "$1" && pwd -P)"
[ -f "$BUNDLE/BUNDLE_COMPLETE" ] \
  || die "$BUNDLE has no BUNDLE_COMPLETE; the job scores only a complete run bundle"
NAME="$(basename "$BUNDLE")"
[[ "$NAME" =~ ^(.+)-arm[0-9]+$ ]] || die "the folder name $NAME is not <basename>-arm<N>"
BASENAME="${BASH_REMATCH[1]}"

if [ $# -eq 2 ]; then
  [ -d "$2" ] || die "the out dir $2 is not a folder"
  OUT_DIR="$(cd "$2" && pwd -P)"
else
  OUT_DIR=""
  dir="$(dirname "$BUNDLE")"
  while :; do
    if [ -f "$dir/$BASENAME.json" ]; then
      OUT_DIR="$dir"
      break
    fi
    [ "$dir" != / ] || break
    dir="$(dirname "$dir")"
  done
  [ -n "$OUT_DIR" ] || die "no folder above $BUNDLE holds $BASENAME.json; give the folder of the result JSON as the second argument"
fi
case "$OUT_DIR/" in
  "$BUNDLE/"*) die "the out dir $OUT_DIR is the bundle folder or inside the bundle folder" ;;
esac
[ ! -e "$OUT_DIR/$NAME.lynx.json" ] \
  || die "$OUT_DIR/$NAME.lynx.json exists; remove it to score the bundle again"

if [ -n "${SLURM_JOB_ID:-}" ]; then
  REPO="${SLURM_SUBMIT_DIR:-$PWD}"
else
  REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd -P)"
fi

if [ -z "${LYNX_SCRATCH:-}" ] && [ -z "$LAB" ]; then
  [ "$DRY_RUN" = true ] || die "set LYNX_LAB (your lab folder under /n/netscratch) or LYNX_SCRATCH"
  echo "lynx_score: LYNX_LAB is not set; a job refuses to start without it or LYNX_SCRATCH" >&2
fi

for path in "$REPO" "$BUNDLE" "$OUT_DIR" "$SCRATCH_DIR"; do
  case "$path" in
    *:* | *,*) die "$path has a ':' or a ','; apptainer --bind cannot take it" ;;
  esac
done

CMD=(
  apptainer exec --nv
  --bind "$REPO" --bind "$BUNDLE" --bind "$OUT_DIR" --bind "$SCRATCH_DIR"
  --pwd "$REPO"
  --env "HF_HOME=$SCRATCH_DIR/hf"
  --env "XDG_CACHE_HOME=$SCRATCH_DIR/cache"
  --env PYTHONNOUSERSITE=1
  "$IMAGE"
  python3 -m scripts.benchmarking.lynx.score "$BUNDLE"
  --model "$MODEL_ID" --revision "$MODEL_REVISION"
  --out-dir "$OUT_DIR" --backend vllm
)

if [ "$DRY_RUN" = true ]; then
  printf '%q' "${CMD[0]}"
  printf ' %q' "${CMD[@]:1}"
  printf '\n'
  exit 0
fi

[ -n "${SLURM_JOB_ID:-}" ] || die "not in a Slurm job; submit it with sbatch, or use --dry-run"
[ -f "$REPO/scripts/benchmarking/lynx/score.py" ] \
  || die "$REPO is not the archi repo root; submit the job from the repo root"
command -v apptainer >/dev/null || die "apptainer is not on PATH"

mkdir -p "$SCRATCH_DIR/hf" "$SCRATCH_DIR/cache" "$SCRATCH_DIR/apptainer"
export APPTAINER_CACHEDIR="$SCRATCH_DIR/apptainer"
echo "lynx_score: job $SLURM_JOB_ID scores $BUNDLE into $OUT_DIR" >&2
exec "${CMD[@]}"

#!/usr/bin/env bash
# Submit one judge-trial job per judge, sized from judges/<judge>.env (JUDGE_GPUS,
# JUDGE_MEM, JUDGE_TIME). Run from the package root on a login node:
#
#   bash scripts/submit.sh gpt-oss-120b nemotron-3-super-fp8
#   bash scripts/submit.sh --all                   # every judges/*.env except llama-3.3-70b
#   bash scripts/submit.sh --partition gpu_requeue gpt-oss-120b
#   DRY=1 bash scripts/submit.sh gpt-oss-120b      # print the sbatch command only
#
# Each job scores every bundle under bundles/ with its judge and skips bundles that
# already have a scores file, so re-submitting a judge after a time-out or a requeue just
# continues. Llama 3.3 70B is gated on Hugging Face and is skipped by --all until its
# weights are in the cache (README, step 1b).
set -euo pipefail

PKG="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
PARTITION="${TRIAL_PARTITION:-}"
JUDGES=()
while [ $# -gt 0 ]; do
  case "$1" in
    --partition) PARTITION="$2"; shift 2 ;;
    --all)
      for f in "$PKG"/judges/*.env; do
        name="$(basename "$f" .env)"
        [ "$name" = llama-3.3-70b ] && continue
        JUDGES+=("$name")
      done
      shift ;;
    -h | --help) sed -n '2,14p' "$0"; exit 0 ;;
    *) JUDGES+=("$1"); shift ;;
  esac
done
[ "${#JUDGES[@]}" -gt 0 ] || { echo "submit: name at least one judge (judges/*.env) or --all" >&2; exit 2; }

value_of() { # $1 = env file, $2 = key
  grep -E "^$2=" "$1" | head -1 | cut -d= -f2- | tr -d '"'"'"
}

mkdir -p "$PKG/logs"
# A judge that cannot go (no judge file, weights missing or still downloading) is reported
# and skipped; the others are still submitted. The exit code is 1 when any was skipped.
failed=0
for judge in "${JUDGES[@]}"; do
  env_file="$PKG/judges/$judge.env"
  [ -f "$env_file" ] || { echo "submit: no $env_file" >&2; failed=1; continue; }
  gpus="$(value_of "$env_file" JUDGE_GPUS)"; gpus="${gpus:-1}"
  mem="$(value_of "$env_file" JUDGE_MEM)"; mem="${mem:-200G}"
  time_limit="$(value_of "$env_file" JUDGE_TIME)"; time_limit="${time_limit:-08:00:00}"
  model="$(value_of "$env_file" JUDGE_MODEL_ID)"
  repo_dir="$PKG/hf/hub/models--${model//\//--}"
  snapshot="$repo_dir/snapshots/$(value_of "$env_file" JUDGE_MODEL_REVISION)"
  # A download in progress already has the snapshot folder; its blobs end in .incomplete.
  if [ ! -f "$snapshot/config.json" ] || [ -n "$(find "$repo_dir/blobs" -name '*.incomplete' -print -quit 2>/dev/null)" ]; then
    echo "submit: $judge: weights at $snapshot are missing or still downloading (README step 1)" >&2
    failed=1
    continue
  fi
  cmd=(sbatch --job-name "archi-judge-trial-$judge" --gres="gpu:$gpus" --mem="$mem" -t "$time_limit"
    --output "logs/archi-judge-trial-$judge-%j.out")
  [ -z "$PARTITION" ] || cmd+=(-p "$PARTITION")
  cmd+=("$PKG/scripts/judge_trial.sbatch" "$judge")
  if [ -n "${DRY:-}" ]; then
    printf '%q ' "${cmd[@]}"; echo
    continue
  fi
  (cd "$PKG" && "${cmd[@]}")
done
exit "$failed"

#!/usr/bin/env bash
#SBATCH -J archi-judge-merge
#SBATCH -p shared
#SBATCH -c 2
#SBATCH --mem=8G
#SBATCH -t 00:30:00
#SBATCH -o archi-judge-merge-%j.out
#
# CPU only. The partition is a setting the operator confirms on fasrc/archi#729.

# Merge the scored judge bundles into judged results (#733; design D8 in
# openspec/changes/archive/2026-10-23-offload-ragas-judge-to-slurm).
#
#   sbatch scripts/benchmarking/slurm/judge_merge.sbatch
#   bash scripts/benchmarking/slurm/judge_merge.sbatch --dry-run
#
# The judge job submits it with --dependency=afterok when it ends, and judge_submit.sh
# submits it for a result whose arms are all SCORED with no MERGED. merge.py runs in the
# scorer image, so the leaderboard and the A/B comparisons are rebuilt with the code of the
# run. It is idempotent: a second merge of a result gives the same bytes and changes nothing.
# --dry-run prints the command and starts nothing.
set -euo pipefail

# --- Settings (the operator confirms them on fasrc/archi#729) -------------------------
JUDGE_HOME="${JUDGE_HOME:-$HOME/archi-judge}"
QUEUE="${JUDGE_QUEUE:-$JUDGE_HOME/queue}"
SCORER_IMAGE="${JUDGE_SCORER_IMAGE:-$JUDGE_HOME/archi-scorer.sif}"
# ---------------------------------------------------------------------------------------

die() {
  echo "judge_merge: refused: $*" >&2
  exit 1
}

DRY_RUN=false
if [ "${1:-}" = --dry-run ]; then
  DRY_RUN=true
  shift
fi
[ $# -eq 0 ] || die "usage: sbatch judge_merge.sbatch | bash judge_merge.sbatch --dry-run"
case "$QUEUE" in
  *:* | *,*) die "$QUEUE has a ':' or a ','; apptainer --bind cannot take it" ;;
esac

CMD=(apptainer exec --cleanenv --bind "$QUEUE" "$SCORER_IMAGE"
  python3 /root/archi/scripts/benchmarking/judge/merge.py --queue "$QUEUE")

if [ "$DRY_RUN" = true ]; then
  printf '%q' "${CMD[0]}"
  printf ' %q' "${CMD[@]:1}"
  printf '\n'
  exit 0
fi

[ -n "${SLURM_JOB_ID:-}" ] || die "not in a Slurm job; submit it with sbatch, or use --dry-run"
command -v apptainer >/dev/null || die "apptainer is not on PATH"
[ -d "$QUEUE" ] || die "the queue folder $QUEUE does not exist"
[ -f "$SCORER_IMAGE" ] || die "no scorer image at $SCORER_IMAGE; build it with build_scorer_image.sh"
echo "judge_merge: job $SLURM_JOB_ID merges the scored results in $QUEUE" >&2
exec "${CMD[@]}"

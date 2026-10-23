#!/usr/bin/env bash
# Submit the offloaded RAGAS judge when its queue has work (#733; design D4 in
# openspec/changes/archive/*-offload-ragas-judge-to-slurm).
#
#   judge_submit.sh            # one tick: submit archi-judge and archi-judge-merge as needed
#   judge_submit.sh --status   # list the queue: READY bundles older than 24 hours, bundles
#                              # that do not match with the reason, stale claims, and the
#                              # SCORER_STALE and INCOMPATIBLE records
#
# scrontab runs one tick every 15 minutes, and a person can run it by hand. One tick:
#   1. counts the available bundles: READY, no SCORED or FAILED, no claim or a stale claim,
#      and matching the configured judge (judge.env) and scorer (scorer-identity.json);
#   2. submits one archi-judge job when the count is 1 or more and no archi-judge job of
#      this user exists. Every archi-judge job has --dependency=singleton, so Slurm runs at
#      most one at a time even when two submitters race;
#   3. submits one archi-judge-merge job when a result has all its arms SCORED, its pending
#      JSON in <queue>/results/ and some arm without MERGED, and no merge job exists. The
#      judge job also submits the merge when it ends; this step covers a judge job that was
#      killed between its last SCORED and that submission.
# A failed squeue submits nothing: an unknown queue state is not an empty one.
set -euo pipefail

# --- Settings (the operator confirms them on fasrc/archi#729) -------------------------
JUDGE_HOME="${JUDGE_HOME:-$HOME/archi-judge}"
QUEUE="${JUDGE_QUEUE:-$JUDGE_HOME/queue}"
JUDGE_ENV="${JUDGE_ENV:-$JUDGE_HOME/judge.env}"
SIDECAR="${JUDGE_SIDECAR:-$JUDGE_HOME/scorer-identity.json}"
# Python 3.8 or later; judge_queue.py uses the standard library only (`module load python`).
JUDGE_PYTHON="${JUDGE_PYTHON:-python3}"
# ---------------------------------------------------------------------------------------

die() {
  echo "judge_submit: $*" >&2
  exit 1
}
log() { echo "judge_submit: $*"; }

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd -P)"
QUEUE_PY="$REPO/scripts/benchmarking/judge/judge_queue.py"

if [ "${1:-}" = --status ]; then
  exec "$JUDGE_PYTHON" "$QUEUE_PY" --queue "$QUEUE" --judge-env "$JUDGE_ENV" --sidecar "$SIDECAR" --status
fi
[ $# -eq 0 ] || die "usage: judge_submit.sh [--status]"
[ -d "$QUEUE" ] || die "the queue folder $QUEUE does not exist"

# The jobs read the same settings; they run in the repo, which Slurm names SLURM_SUBMIT_DIR.
export ARCHI_REPO="$REPO" JUDGE_HOME JUDGE_QUEUE="$QUEUE" JUDGE_ENV JUDGE_SIDECAR="$SIDECAR" JUDGE_PYTHON
cd "$REPO"

jobs_named() { # $1 = job name: the ids of this user's jobs of that name; fails when squeue fails
  squeue -h -u "$USER" -n "$1" -o %i || die "squeue failed; nothing submitted"
}

JUDGE_JOBS="$(jobs_named archi-judge)"
if [ -n "$JUDGE_JOBS" ]; then
  log "archi-judge job $(echo $JUDGE_JOBS) exists; no judge job submitted"
else
  COUNT="$("$JUDGE_PYTHON" "$QUEUE_PY" --queue "$QUEUE" --judge-env "$JUDGE_ENV" --sidecar "$SIDECAR" --count)" \
    || die "judge_queue.py could not count the bundles (see above); nothing submitted"
  if [ "$COUNT" -ge 1 ]; then
    log "$COUNT available bundle(s); submitting archi-judge"
    sbatch --parsable --dependency=singleton "$REPO/scripts/benchmarking/slurm/archi_judge.sbatch"
  else
    log "no available bundle; no judge job submitted"
  fi
fi

READY="$("$JUDGE_PYTHON" "$QUEUE_PY" --queue "$QUEUE" --merge-ready)" \
  || die "judge_queue.py could not list the results to merge (see above)"
if [ -n "$READY" ]; then
  MERGE_JOBS="$(jobs_named archi-judge-merge)"
  if [ -n "$MERGE_JOBS" ]; then
    log "archi-judge-merge job $(echo $MERGE_JOBS) exists; no merge job submitted"
  else
    log "ready to merge: $(echo $READY); submitting archi-judge-merge"
    sbatch --parsable "$REPO/scripts/benchmarking/slurm/judge_merge.sbatch"
  fi
fi

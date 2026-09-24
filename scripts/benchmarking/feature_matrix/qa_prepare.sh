#!/usr/bin/env bash
# Prepare a prompt sweep's gold atoms ONCE, before the sweep is locked.
#
#   qa_prepare.sh --sweep <stack> --qa-dataset <qa-v2.json> --qa-profile <yaml> [--force]
#
# The converted bank supplies references rather than expected atoms, so every
# `archi eval qa` run extracts atoms with the evaluator model
# (src/evaluation/qa/preparation.py:305-320) and two runs can grade the same item against
# different atoms. Plan W8: "Prepare the gold atoms once and run every arm against that one
# snapshot". This writes $FM_OUT/qa/<stack>-prepared; lock_campaign.sh --sweep pins its
# preparation.jsonl, and every qa_arm.sh --sweep run copies it. Refused once the sweep is
# locked, so the lock and the stack stamps taken from it never go stale.
# Needs HUIT_API_KEY_FILE (the evaluator key) in the environment.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

STACK=""; DATASET=""; PROFILE=""; FORCE=false
while [ $# -gt 0 ]; do
  case "$1" in
    --sweep) STACK="${2:?--sweep needs the stack name}"; shift 2 ;;
    --qa-dataset) DATASET="${2:?}"; shift 2 ;;
    --qa-profile) PROFILE="${2:?}"; shift 2 ;;
    --force) FORCE=true; shift ;;
    -h|--help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) fm_die "unknown option $1" ;;
  esac
done
fm_require_stack_name "$STACK"
[ -f "$DATASET" ] || fm_die "QA dataset not found: '${DATASET}'"
[ -f "$PROFILE" ] || fm_die "evaluator profile not found: '${PROFILE}'"
[ ! -e "$(fm_sweep_lock_file "$STACK")" ] || fm_die "sweep $STACK is already locked; preparing again would change the atoms the lock pins"
OUT="$FM_OUT/qa/$STACK-prepared"
if [ -e "$OUT" ]; then
  [ "$FORCE" = true ] || fm_die "prepared workspace exists: $OUT (pass --force to replace it before locking)"
  rm -rf "$OUT"
fi
mkdir -p "$FM_OUT/qa"
fm_log "preparing gold atoms for sweep $STACK → $OUT"
"$FM_ARCHI" eval qa prepare "$DATASET" --evaluator-profile "$PROFILE" --output-dir "$OUT"
fm_log "next: lock_campaign.sh --sweep <sweep_dir> --manifest <yaml> --stack $STACK --qa-dataset $DATASET --qa-profile $PROFILE"

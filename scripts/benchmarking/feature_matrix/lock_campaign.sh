#!/usr/bin/env bash
# Lock the campaign's fixed factors (plan §6 step 3; pre-registration gate G1).
#
#   lock_campaign.sh <00-baseline.yaml> --qa-dataset <qa-v2.json> [--qa-profile <yaml>] [--relock]
#
# Hashes every input the pre-registration pins — the bank, the anchors, the prompt, the
# sources list(s), the QA dataset and evaluator profile — and records the SUT, judge and
# metric settings, from the baseline arm YAML, into $FM_OUT/campaign.lock. From then on
# every wrapper refuses an arm YAML, dataset, profile or spec whose CONTENT differs from
# the lock; which file an operator names no longer decides acceptance. An existing lock is
# never overwritten without --relock, and a re-lock is itself recorded in the ledger so the
# pre-registration can be re-locked with it. Run from ~/Projects/archi.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

YAML="${1:-}"; fm_require_arm_yaml 00 "$YAML"; shift
QA_DATASET=""; QA_PROFILE="config/benchmarking/feature_matrix/qa/evaluator-profile.huit.yaml"; RELOCK=false
while [ $# -gt 0 ]; do
  case "$1" in
    --qa-dataset) QA_DATASET="${2:?}"; shift 2 ;;
    --qa-profile) QA_PROFILE="${2:?}"; shift 2 ;;
    --relock) RELOCK=true; shift ;;
    -h|--help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) fm_die "unknown option $1" ;;
  esac
done
[ -n "$QA_DATASET" ] && [ -f "$QA_DATASET" ] || fm_die "--qa-dataset must name the converted bank (ragas_bank_to_qa_dataset.py output); got '${QA_DATASET}'"
[ -f "$QA_PROFILE" ] || fm_die "evaluator profile not found: $QA_PROFILE"
LOCK="$(fm_lock_file)"
if [ -f "$LOCK" ] && [ "$RELOCK" != true ]; then
  fm_die "campaign already locked at $LOCK (pass --relock to replace it; the pre-registration must be re-locked too)"
fi
mkdir -p "$FM_OUT"
CODE_SHA="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
FM_FACTORS="$(fm_fixed_factors_json "$YAML")" FM_LOCK="$LOCK" FM_QA_DATASET="$QA_DATASET" FM_QA_PROFILE="$QA_PROFILE" \
FM_CODE_SHA="$CODE_SHA" FM_BASELINE="$YAML" FM_LOCKED="$(fm_now)" "$FM_PYTHON" - <<'EOF'
import hashlib, json, os
sha = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()
lock = json.loads(os.environ["FM_FACTORS"])
lock.update({"locked": os.environ["FM_LOCKED"], "baseline_yaml": os.environ["FM_BASELINE"], "code_sha": os.environ["FM_CODE_SHA"],
             "cwd": os.getcwd(),
             "qa": {"dataset": os.environ["FM_QA_DATASET"], "dataset_sha256": sha(os.environ["FM_QA_DATASET"]),
                    "profile": os.environ["FM_QA_PROFILE"], "profile_sha256": sha(os.environ["FM_QA_PROFILE"])}})
with open(os.environ["FM_LOCK"], "w") as f:
    json.dump(lock, f, indent=1, sort_keys=True)
print("locked fixed factors:")
for k, v in lock["files"].items():
    print(f"  {k:12s} {v['sha256'][:12]}  {v['path']}")
for k, v in lock["values"].items():
    print(f"  {k:22s} {v!r}"[:110])
print(f"  qa dataset   {lock['qa']['dataset_sha256'][:12]}  {lock['qa']['dataset']}")
print(f"  qa profile   {lock['qa']['profile_sha256'][:12]}  {lock['qa']['profile']}")
EOF
fm_ledger_append "$(printf '{"kind":"lock","relock":%s,"locked":"%s","lock_sha256":"%s","baseline_yaml":"%s","code_sha":"%s"}' "$RELOCK" "$(fm_now)" "$(fm_lock_sha)" "$YAML" "$CODE_SHA")"
fm_log "campaign lock written: $LOCK (sha256 $(fm_lock_sha | cut -c1-12)); record this hash in the pre-registration"

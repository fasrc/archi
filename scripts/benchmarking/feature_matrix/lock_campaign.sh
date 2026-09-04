#!/usr/bin/env bash
# Lock the campaign's fixed factors (plan §6 step 3; pre-registration gate G1).
#
#   lock_campaign.sh <00-baseline.yaml> --arms-dir <dir> --qa-dataset <qa-v2.json> [--qa-profile <yaml>] [--relock]
#
# Hashes every input the pre-registration pins — the bank, the anchors, the prompt, the
# sources list(s), the QA dataset and evaluator profile — records the SUT, judge and
# metric settings and every non-factor data_manager setting from the baseline arm YAML,
# pins the sha256 of EVERY arm YAML in --arms-dir (keyed by its `name: fm-<arm>` label, so
# each arm's treatment value is locked too), and pins the runtime code (tree ids of src/,
# scripts/, deploy/, pyproject.toml, requirements/) into $FM_OUT/campaign.lock. From then
# on every wrapper refuses an arm YAML, dataset, profile or spec whose CONTENT differs from
# the lock, and a stack deployed under a different lock; which file an operator names no
# longer decides acceptance. An existing lock is never overwritten without --relock, and a
# re-lock is recorded in the ledger so the pre-registration can be re-locked with it —
# and every stack deployed before it must be redeployed. Run from ~/Projects/archi.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

YAML="${1:-}"; fm_require_arm_yaml 00 "$YAML"; shift
QA_DATASET=""; QA_PROFILE="config/benchmarking/feature_matrix/qa/evaluator-profile.huit.yaml"; RELOCK=false; ARMS_DIR=""
while [ $# -gt 0 ]; do
  case "$1" in
    --arms-dir) ARMS_DIR="${2:?}"; shift 2 ;;
    --qa-dataset) QA_DATASET="${2:?}"; shift 2 ;;
    --qa-profile) QA_PROFILE="${2:?}"; shift 2 ;;
    --relock) RELOCK=true; shift ;;
    -h|--help) sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) fm_die "unknown option $1" ;;
  esac
done
[ -n "$QA_DATASET" ] && [ -f "$QA_DATASET" ] || fm_die "--qa-dataset must name the converted bank (ragas_bank_to_qa_dataset.py output); got '${QA_DATASET}'"
[ -f "$QA_PROFILE" ] || fm_die "evaluator profile not found: $QA_PROFILE"
[ -n "$ARMS_DIR" ] && [ -d "$ARMS_DIR" ] || fm_die "--arms-dir must name the directory holding every arm YAML (config/benchmarking/feature_matrix); got '${ARMS_DIR}'"
[ "$(cd "$(dirname "$YAML")" && pwd)" = "$(cd "$ARMS_DIR" && pwd)" ] || fm_die "the baseline YAML must live inside --arms-dir"
LOCK="$(fm_lock_file)"
if [ -f "$LOCK" ] && [ "$RELOCK" != true ]; then
  fm_die "campaign already locked at $LOCK (pass --relock to replace it; the pre-registration must be re-locked too)"
fi
mkdir -p "$FM_OUT"
CODE_SHA="$(fm_code_sha)"
[ "$CODE_SHA" != unknown ] || fm_die "not inside a git checkout — the lock must pin the code revision the campaign runs"
DIRTY="$("$FM_GIT" status --porcelain --untracked-files=no -- src scripts deploy 2>/dev/null || true)"
[ -z "$DIRTY" ] || fm_die "commit or stash source changes before locking; the lock pins a commit, not a dirty tree:
$DIRTY"
FM_FACTORS="$(fm_fixed_factors_json "$YAML")" FM_LOCK="$LOCK" FM_QA_DATASET="$QA_DATASET" FM_QA_PROFILE="$QA_PROFILE" FM_ARMS_DIR="$ARMS_DIR" FM_KEYS="$FM_FACTOR_KEYS" \
FM_CODE_SHA="$CODE_SHA" FM_CODE_TREE="$(fm_code_tree)" FM_BASELINE="$YAML" FM_LOCKED="$(fm_now)" "$FM_PYTHON" - <<'EOF'
import glob, hashlib, json, os, sys, yaml
sha = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()
lock = json.loads(os.environ["FM_FACTORS"])
# the arm manifest: every YAML in the arms dir, keyed by its own label, with its factors
def get(m, path):
    for k in path.split("."):
        if not isinstance(m, dict) or k not in m:
            return None
        m = m[k]
    return m
arms = {}
for path in sorted(glob.glob(os.path.join(os.environ["FM_ARMS_DIR"], "*.yaml"))):
    cfg = yaml.safe_load(open(path)) or {}
    name = str(cfg.get("name", ""))
    if not name.startswith("fm-"):
        sys.exit(f"{path}: name {name!r} is not an arm label (want fm-<arm>)")
    label = name[3:]
    if label in arms:
        sys.exit(f"two files in the arms dir carry the label {label}: {arms[label]['file']} and {path}")
    arms[label] = {"file": path, "sha256": sha(path),
                   "factors": {k: get(cfg.get("data_manager") or {}, k) for k in os.environ["FM_KEYS"].split()}}
if not arms:
    sys.exit("the arms dir holds no *.yaml")
lock.update({"locked": os.environ["FM_LOCKED"], "baseline_yaml": os.environ["FM_BASELINE"], "arms": arms,
             "code_sha": os.environ["FM_CODE_SHA"], "code_tree": os.environ["FM_CODE_TREE"],
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
print(f"  code         HEAD {lock['code_sha'][:12]}; runtime trees {lock['code_tree']}")
print("  arms         " + ", ".join(f"{k}={v['sha256'][:8]}" for k, v in sorted(lock["arms"].items())))
EOF
fm_ledger_append "$(printf '{"kind":"lock","relock":%s,"locked":"%s","lock_sha256":"%s","baseline_yaml":"%s","code_sha":"%s"}' "$RELOCK" "$(fm_now)" "$(fm_lock_sha)" "$YAML" "$CODE_SHA")"
fm_log "campaign lock written: $LOCK (sha256 $(fm_lock_sha | cut -c1-12)); record this hash in the pre-registration"

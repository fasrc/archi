#!/usr/bin/env bash
# Record one finished RAGAS run in the campaign ledger (plan §5.1, §9).
#
#   archive_run.sh <arm> <run> <arm.yaml> [--stack <name>] [--wait] [--new-corpus]
#
# Waits (with --wait) for benchmarking-<stack> to exit, takes the newest artifact
# benchmarking-<stack>-*.json in FM_OUT, and REFUSES when
#   - the arm YAML's `name` is not fm-<arm>, or the artifact's recorded running
#     configuration disagrees with the arm YAML on any factor key (the artifact itself
#     proves which arm ran — the operator's label alone never does),
#   - the artifact is already in the ledger, the (arm, stack, run) identity is already
#     archived, a run other than 1 arrives before the stack has a pin, the artifact is
#     older than the stack's latest `ragas-start` entry (a re-run that wrote nothing must
#     not re-archive run 1 as run 2), or that start row was made under a lock other than
#     the active one (a --relock happened since the run started),
#   - the corpus changed during the run: `corpus_fingerprint_before` must equal
#     `corpus_fingerprint` and `corpus_unchanged_at_endpoints` must be true,
#   - the artifact's config_version.divergence_from_selected_file is non-empty (the run
#     did not use the settings you selected — Procedure E of interpreting_benchmark_results),
#   - a corpus pin exists for the stack and the artifact's fingerprint differs. The one
#     legitimate re-pin is the closing baseline (plan §6 step 7): --new-corpus is honoured
#     only for arm 00, only when the stack's latest ragas-start was a fresh deploy (not a
#     re-run or re-seed), and the old and new fingerprints are both recorded in the row.
#   - the arm YAML's fixed factors differ from the campaign lock, the YAML is not the locked
#     file for that arm label, or the stack was deployed under a different lock,
#   - no ragas-start row exists for the stack (nothing ties the artifact to a lock or a
#     start time), or the live document/chunk counts cannot be read.
# On run 1 of a stack it writes the corpus pin every later re-run and re-seed checks.
# Appends: fingerprint, snapshot id, config + code digests, ingest_wall_seconds, live
# document and chunk counts (from the stack's Postgres), per-metric scored counts
# recomputed from finite values (#279), and the degraded-row count.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

ARM="${1:-}"; fm_require_arm "$ARM"; RUN="${2:-}"; [ -n "$RUN" ] || fm_die "usage: archive_run.sh <arm> <run> <arm.yaml> [--stack <name>] [--wait] [--new-corpus]"; fm_require_run_number "$RUN"
YAML="${3:-}"; fm_require_arm_yaml "$ARM" "$YAML"; shift 3
fm_require_lock "$YAML"
fm_require_locked_arm "$ARM" "$YAML"
STACK="fm-$ARM"; WAIT=false; NEW_CORPUS=false
while [ $# -gt 0 ]; do
  case "$1" in
    --stack) STACK="${2:?}"; shift 2 ;;
    --wait) WAIT=true; shift ;;
    --new-corpus) NEW_CORPUS=true; shift ;;
    -h|--help) sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) fm_die "unknown option $1" ;;
  esac
done

fm_require_stack_lock "$STACK"          # the artifact comes from a stack deployed under the ACTIVE lock
if [ "$WAIT" = true ]; then
  while [ "$(fm_container_state "benchmarking-$STACK")" = "running" ]; do sleep "${FM_POLL_SECONDS:-30}"; done
fi
[ "$(fm_container_state "benchmarking-$STACK")" != "running" ] || fm_die "benchmarking-$STACK is still running (use --wait)"

ARTIFACT="$(ls -t "$FM_OUT"/benchmarking-"$STACK"-*.json 2>/dev/null | head -1 || true)"
[ -n "$ARTIFACT" ] || fm_die "no artifact benchmarking-$STACK-*.json under $FM_OUT"

# One artifact, one ledger row: refuse a file already archived, and a file that predates
# this stack's latest ragas-start (the re-run produced nothing; this is run 1's file).
FM_LEDGER="$(fm_ledger)" FM_ARTIFACT="$ARTIFACT" FM_STACK="$STACK" FM_ARM="$ARM" FM_RUN="$RUN" FM_PIN_FILE="$(fm_pin_file "$STACK")" FM_ACTIVE_LOCK_SHA="$(fm_lock_sha)" "$FM_PYTHON" - <<'EOF' || fm_die "refusing to archive $ARTIFACT (see above)"
import datetime as dt, json, os, sys
ledger, artifact, stack = os.environ["FM_LEDGER"], os.environ["FM_ARTIFACT"], os.environ["FM_STACK"]
arm, run = os.environ["FM_ARM"], int(os.environ["FM_RUN"])
rows = json.load(open(ledger)) if os.path.exists(ledger) else []
dup = [r for r in rows if r.get("artifact") == artifact]
if dup:
    print(f"artifact already archived as arm {dup[0].get('arm')} run {dup[0].get('run')}", file=sys.stderr); sys.exit(1)
same = [r for r in rows if r.get("kind") == "ragas" and r.get("arm") == arm and r.get("stack") == stack and r.get("run") == run]
if same:
    print(f"arm {arm} run {run} on {stack} is already archived ({same[0].get('artifact')}); pick the next run number", file=sys.stderr); sys.exit(1)
# The corpus pin is established by run 1 and nothing else: a later run number archived
# first would make an arbitrary artifact the reference corpus.
if not os.path.exists(os.environ["FM_PIN_FILE"]) and run != 1:
    print(f"no corpus pin for {stack} yet — archive run 1 first (got run {run})", file=sys.stderr); sys.exit(1)
start_rows = [r for r in rows if r.get("kind") == "ragas-start" and r.get("stack") == stack and r.get("started")]
if not start_rows:
    # No wrapper started a run on this stack: nothing ties the artifact to a lock or a
    # start time, so the age and lock checks below would be skipped. Not optional.
    print(f"no ragas-start row for {stack} in the ledger — the run was not started by run_arm.sh/reseed_arm.sh under the campaign lock; it cannot be archived", file=sys.stderr); sys.exit(1)
if True:
    # `started` has second resolution (fm_now), so several rows can share the newest
    # second. Break that tie by ledger position: the ledger is append-ordered, so the
    # last of them is the run that started — the same rule the --new-corpus check below
    # applies with starts[-1]. Without the tie-break max() returns the first row it sees,
    # which is the oldest, and a stale row from before a --relock decides the lock check.
    latest_row = max(enumerate(start_rows), key=lambda p: (p[1]["started"], p[0]))[1]
    latest = dt.datetime.fromisoformat(latest_row["started"].replace("Z", "+00:00"))
    mtime = dt.datetime.fromtimestamp(os.path.getmtime(artifact), tz=dt.timezone.utc)
    if mtime < latest:
        print(f"artifact written {mtime:%Y-%m-%dT%H:%M:%SZ}, before the latest ragas-start for {stack} at {latest:%Y-%m-%dT%H:%M:%SZ} — the run produced no new artifact", file=sys.stderr); sys.exit(1)
    # A run started under an earlier lock (a --relock happened since) may have used the
    # previous bank, prompt, SUT or judge; it is not evidence under the active lock.
    if latest_row.get("lock_sha256") != os.environ["FM_ACTIVE_LOCK_SHA"]:
        print(f"the run that produced this artifact started under lock {str(latest_row.get('lock_sha256'))[:12]}, not the active lock {os.environ['FM_ACTIVE_LOCK_SHA'][:12]} — re-run it under the current lock", file=sys.stderr); sys.exit(1)
EOF

# The live document and chunk counts are part of every arm's cost report and the stack is
# deleted right after archiving, so they must be read NOW or never: refuse on failure.
DOCS="$("$FM_DOCKER" exec "postgres-$STACK" psql -U archi -d archi-db -tAc "select count(*) from documents where is_deleted is not true;" 2>/dev/null | tr -d '[:space:]' || true)"
CHUNKS="$("$FM_DOCKER" exec "postgres-$STACK" psql -U archi -d archi-db -tAc "select count(*) from document_chunks;" 2>/dev/null | tr -d '[:space:]' || true)"
[[ "$DOCS" =~ ^[0-9]+$ && "$CHUNKS" =~ ^[0-9]+$ ]] || fm_die "could not read the live document/chunk counts from postgres-$STACK (got docs='${DOCS}' chunks='${CHUNKS}'); the stack must be up when a run is archived"

PIN_FILE="$(fm_pin_file "$STACK")"
ENTRY="$(FM_ARTIFACT="$ARTIFACT" FM_ARM="$ARM" FM_RUN="$RUN" FM_STACK="$STACK" FM_DOCS="$DOCS" FM_CHUNKS="$CHUNKS" FM_ARM_YAML="$YAML" FM_KEYS="$FM_FACTOR_KEYS" \
  FM_LEDGER="$(fm_ledger)" FM_LOCK_SHA="$(fm_lock_sha)" \
  FM_PIN_FILE="$PIN_FILE" FM_NEW_CORPUS="$NEW_CORPUS" FM_FINISHED="$(fm_now)" "$FM_PYTHON" - <<'EOF'
import json, math, os, sys, yaml
p = os.environ["FM_ARTIFACT"]
d = json.loads(open(p).read().replace("NaN", "null"))          # pre-#279 artifacts carry bare NaN
arms = d["benchmarking_results"]
if len(arms) != 1:
    print(f"expected one arm in {p}, found {len(arms)} — a stray file in the stack's configs/ ran as an arm", file=sys.stderr); sys.exit(2)
arm = arms[0]
# The artifact proves which arm ran: its recorded running configuration (what the agent
# read from Postgres, #269) must agree with the arm YAML on EVERY factor key; a key missing
# on either side is a mismatch. An artifact without running_configuration (pre-#269) cannot
# prove anything — its `configuration` is a disk re-read — and is refused. A label typed by
# the operator never decides this.
running = arm.get("running_configuration")
if not isinstance(running, dict):
    print("REFUSED: artifact carries no running_configuration (pre-#269 harness); it cannot prove which arm ran", file=sys.stderr); sys.exit(2)
recorded = running.get("data_manager") or {}
wanted = (yaml.safe_load(open(os.environ["FM_ARM_YAML"])) or {}).get("data_manager") or {}
def get(m, path):
    for k in path.split("."):
        if not isinstance(m, dict) or k not in m:
            return None
        m = m[k]
    return m
mismatch = [(k, get(wanted, k), get(recorded, k)) for k in os.environ["FM_KEYS"].split()
            if get(wanted, k) is None or get(recorded, k) is None or get(wanted, k) != get(recorded, k)]
if mismatch:
    for key, w, r in mismatch:
        print(f"REFUSED: artifact ran factor {key}={r!r}, arm {os.environ['FM_ARM']} wants {w!r}", file=sys.stderr)
    sys.exit(2)
cv = arm.get("config_version") or {}
div = cv.get("divergence_from_selected_file")
if div:
    print(f"REFUSED: the run did not use the selected settings — divergence_from_selected_file = {div}", file=sys.stderr); sys.exit(2)
fp = arm.get("corpus_fingerprint")
fp_before = arm.get("corpus_fingerprint_before")
def usable(x): return isinstance(x, str) and x.startswith("sha256:")
if not usable(fp) or not usable(fp_before):
    print(f"REFUSED: the artifact lacks usable endpoint fingerprints (before={fp_before!r}, after={fp!r})", file=sys.stderr); sys.exit(2)
# The harness samples the corpus at both ends of the arm. Questions scored across two
# corpora are not one observation; such an artifact must never become the pin.
if fp != fp_before or arm.get("corpus_unchanged_at_endpoints") is not True:
    print(f"REFUSED: the corpus changed during the run (before {fp_before}, after {fp}, unchanged={arm.get('corpus_unchanged_at_endpoints')!r}); the arm is void", file=sys.stderr); sys.exit(2)
pin_file, run = os.environ["FM_PIN_FILE"], int(os.environ["FM_RUN"])
previous_pin = None
if os.path.exists(pin_file):
    pin = open(pin_file).read().strip()
    if fp != pin:
        if os.environ["FM_NEW_CORPUS"] != "true":
            print(f"REFUSED: fingerprint {fp} != pin {pin} for this stack (a re-pin is only the closing baseline: arm 00, fresh deploy, --new-corpus)", file=sys.stderr); sys.exit(2)
        # --new-corpus is structural, not a bare flag: only the baseline arm, and only when
        # this stack's latest ragas-start was a fresh deploy, may move the pin.
        if os.environ["FM_ARM"] != "00":
            print(f"REFUSED: --new-corpus is only valid for arm 00 (the closing baseline), not arm {os.environ['FM_ARM']}", file=sys.stderr); sys.exit(2)
        ledger_path = os.environ["FM_LEDGER"]
        rows = json.load(open(ledger_path)) if os.path.exists(ledger_path) else []
        starts = [r for r in rows if r.get("kind") == "ragas-start" and r.get("stack") == os.environ["FM_STACK"]]
        if not starts or starts[-1].get("rerun") is not False:
            print("REFUSED: --new-corpus needs a fresh deploy (run_arm.sh <arm> <yaml>) as this stack's latest ragas-start; the latest was a re-run or re-seed", file=sys.stderr); sys.exit(2)
        previous_pin = pin
        open(pin_file, "w").write(fp + "\n")
else:
    open(pin_file, "w").write(fp + "\n")
rows = arm.get("single_question_results") or {}
def finite(x): return isinstance(x, (int, float)) and math.isfinite(x)
metrics = sorted({k for r in rows.values() for k in r if k in ("answer_relevancy", "faithfulness", "context_precision", "context_recall", "answer_correctness")})
scored = {m: f"{sum(1 for r in rows.values() if r.get('status', 'ok') == 'ok' and finite(r.get(m)))} of {len(rows)}" for m in metrics}
def num(s):
    try: return int(s)
    except (TypeError, ValueError): return None
entry = {
    "arm": os.environ["FM_ARM"], "kind": "ragas", "run": run, "stack": os.environ["FM_STACK"],
    "finished": os.environ["FM_FINISHED"], "artifact": p,
    "arm_config": os.environ["FM_ARM_YAML"], "configuration_file": arm.get("configuration_file"),
    "corpus_fingerprint": fp, "corpus_fingerprint_before": fp_before, "fingerprint_source": "artifact", "repinned_from": previous_pin,
    "lock_sha256": os.environ["FM_LOCK_SHA"],
    "corpus_snapshot_id": (d.get("metadata") or {}).get("corpus_snapshot_id"),
    "config_digest": cv.get("digest"), "code_digest": ((d.get("metadata") or {}).get("code_version") or {}).get("digest"),
    "ingest_wall_seconds": arm.get("ingest_wall_seconds", "not recorded"),
    "documents": num(os.environ["FM_DOCS"]), "chunks": num(os.environ["FM_CHUNKS"]),
    "questions": len(rows), "degraded": sum(1 for r in rows.values() if r.get("status") == "degraded"),
    "scored": scored,
    "aggregates": {m: arm.get("total_results", {}).get(f"aggregate_{m}") for m in metrics},
}
print(json.dumps(entry))
EOF
)"
fm_ledger_append "$ENTRY"
fm_log "archived arm $ARM run $RUN: $ARTIFACT"
FM_ENTRY="$ENTRY" "$FM_PYTHON" - <<'EOF'
import json, os
e = json.loads(os.environ["FM_ENTRY"])
print("    fingerprint %s  docs %s  chunks %s  degraded %s  ingest_s %s"
      % (e["corpus_fingerprint"], e["documents"], e["chunks"], e["degraded"], e["ingest_wall_seconds"]))
for m, v in e["scored"].items():
    print("    %-18s %s   agg %s" % (m, v, e["aggregates"][m]))
EOF

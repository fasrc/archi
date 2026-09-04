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
#   - the artifact is already in the ledger, or is older than the stack's latest
#     `ragas-start` entry (a re-run that wrote nothing must not re-archive run 1 as run 2),
#   - the artifact's config_version.divergence_from_selected_file is non-empty (the run
#     did not use the settings you selected — Procedure E of interpreting_benchmark_results),
#   - a corpus pin exists for the stack and the artifact's fingerprint differs (unless
#     --new-corpus, for the closing baseline's fresh ingest on a reused stack name).
# On run 1 of a stack it writes the corpus pin every later re-run and re-seed checks.
# Appends: fingerprint, snapshot id, config + code digests, ingest_wall_seconds, live
# document and chunk counts (from the stack's Postgres), per-metric scored counts
# recomputed from finite values (#279), and the degraded-row count.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

ARM="${1:-}"; fm_require_arm "$ARM"; RUN="${2:-}"; [[ "$RUN" =~ ^[0-9]+$ ]] || fm_die "usage: archive_run.sh <arm> <run> <arm.yaml> [--stack <name>] [--wait] [--new-corpus]"
YAML="${3:-}"; fm_require_arm_yaml "$ARM" "$YAML"; shift 3
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

if [ "$WAIT" = true ]; then
  while [ "$(fm_container_state "benchmarking-$STACK")" = "running" ]; do sleep "${FM_POLL_SECONDS:-30}"; done
fi
[ "$(fm_container_state "benchmarking-$STACK")" != "running" ] || fm_die "benchmarking-$STACK is still running (use --wait)"

ARTIFACT="$(ls -t "$FM_OUT"/benchmarking-"$STACK"-*.json 2>/dev/null | head -1 || true)"
[ -n "$ARTIFACT" ] || fm_die "no artifact benchmarking-$STACK-*.json under $FM_OUT"

# One artifact, one ledger row: refuse a file already archived, and a file that predates
# this stack's latest ragas-start (the re-run produced nothing; this is run 1's file).
FM_LEDGER="$(fm_ledger)" FM_ARTIFACT="$ARTIFACT" FM_STACK="$STACK" "$FM_PYTHON" - <<'EOF' || fm_die "refusing to archive $ARTIFACT (see above)"
import datetime as dt, json, os, sys
ledger, artifact, stack = os.environ["FM_LEDGER"], os.environ["FM_ARTIFACT"], os.environ["FM_STACK"]
rows = json.load(open(ledger)) if os.path.exists(ledger) else []
dup = [r for r in rows if r.get("artifact") == artifact]
if dup:
    print(f"artifact already archived as arm {dup[0].get('arm')} run {dup[0].get('run')}", file=sys.stderr); sys.exit(1)
starts = [r["started"] for r in rows if r.get("kind") == "ragas-start" and r.get("stack") == stack and r.get("started")]
if starts:
    latest = max(dt.datetime.fromisoformat(s.replace("Z", "+00:00")) for s in starts)
    mtime = dt.datetime.fromtimestamp(os.path.getmtime(artifact), tz=dt.timezone.utc)
    if mtime < latest:
        print(f"artifact written {mtime:%Y-%m-%dT%H:%M:%SZ}, before the latest ragas-start for {stack} at {latest:%Y-%m-%dT%H:%M:%SZ} — the run produced no new artifact", file=sys.stderr); sys.exit(1)
EOF

DOCS="$("$FM_DOCKER" exec "postgres-$STACK" psql -U archi -d archi-db -tAc "select count(*) from documents where is_deleted is not true;" 2>/dev/null | tr -d '[:space:]' || echo null)"
CHUNKS="$("$FM_DOCKER" exec "postgres-$STACK" psql -U archi -d archi-db -tAc "select count(*) from document_chunks;" 2>/dev/null | tr -d '[:space:]' || echo null)"
[ -n "$DOCS" ] || DOCS=null; [ -n "$CHUNKS" ] || CHUNKS=null

PIN_FILE="$(fm_pin_file "$STACK")"
ENTRY="$(FM_ARTIFACT="$ARTIFACT" FM_ARM="$ARM" FM_RUN="$RUN" FM_STACK="$STACK" FM_DOCS="$DOCS" FM_CHUNKS="$CHUNKS" FM_ARM_YAML="$YAML" FM_KEYS="$FM_FACTOR_KEYS" \
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
fp = arm.get("corpus_fingerprint") or (d.get("metadata") or {}).get("corpus_fingerprint")
if not isinstance(fp, str) or fp.startswith("<unavailable"):
    print(f"REFUSED: no usable corpus_fingerprint in {p} (got {fp!r})", file=sys.stderr); sys.exit(2)
pin_file, run = os.environ["FM_PIN_FILE"], int(os.environ["FM_RUN"])
if os.path.exists(pin_file):
    pin = open(pin_file).read().strip()
    if fp != pin and os.environ["FM_NEW_CORPUS"] != "true":
        print(f"REFUSED: fingerprint {fp} != pin {pin} for this stack (pass --new-corpus only for a deliberate fresh ingest)", file=sys.stderr); sys.exit(2)
    if fp != pin:
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
    "corpus_fingerprint": fp, "fingerprint_source": "artifact",
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

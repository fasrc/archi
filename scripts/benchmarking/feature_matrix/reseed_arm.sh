#!/usr/bin/env bash
# Switch a running stack to a RETRIEVAL-side arm without re-ingesting (plan §5.2).
#
#   reseed_arm.sh <arm> <arm.yaml> [--stack fm-00]
#
# Copies the arm's retrieval keys (data_manager.retrievers.hierarchical_rerank.*) into the
# stack's rendered configs/config.yaml, re-runs the one-shot config-seed container (which
# upserts static_config — the agent reads it at boot), then starts the benchmark container.
# Postgres and the data-manager are never restarted; the corpus fingerprint is checked
# before and after. Refuses an arm whose change is INGEST-side (chunking, processing,
# stemming): a re-seed cannot re-chunk what is already stored, so such an arm would run on
# the wrong corpus and look plausible.
#
# The rendered config is backed up ONCE to <stack>/fm-backup/config.yaml — outside
# configs/, because the harness sweeps every file in configs/ as an arm.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

ARM="${1:-}"; fm_require_arm "$ARM"; YAML="${2:-}"; shift 2 || fm_die "usage: reseed_arm.sh <arm> <arm.yaml> [--stack <name>]"
STACK="fm-00"
while [ $# -gt 0 ]; do
  case "$1" in
    --stack) STACK="${2:?--stack needs a name}"; shift 2 ;;
    -h|--help) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) fm_die "unknown option $1" ;;
  esac
done
fm_require_arm_yaml "$ARM" "$YAML"
STACK_DIR="$(fm_stack_dir "$STACK")"
RENDERED="$STACK_DIR/configs/config.yaml"
[ -f "$RENDERED" ] || fm_die "no rendered config at $RENDERED"
fm_require_stack_up "$STACK"
[ "$(fm_container_state "benchmarking-$STACK")" != "running" ] || fm_die "a benchmark run is still in flight on $STACK"
fm_require_pinned_corpus "$STACK"

mkdir -p "$STACK_DIR/fm-backup"
[ -f "$STACK_DIR/fm-backup/config.yaml" ] || cp "$RENDERED" "$STACK_DIR/fm-backup/config.yaml"

# Refuse ingest-side differences; copy retrieval-side keys. Exit 3 = refused as ingest-side.
FM_ARM_YAML="$YAML" FM_RENDERED="$RENDERED" "$FM_PYTHON" - <<'EOF' || { rc=$?; [ "$rc" = 3 ] && fm_die "arm $ARM changes an ingest-side key; it needs its own stack (run_arm.sh), not a re-seed"; exit "$rc"; }
import os, sys, yaml
arm = yaml.safe_load(open(os.environ["FM_ARM_YAML"]))["data_manager"]
rendered = yaml.safe_load(open(os.environ["FM_RENDERED"]))
dm = rendered["data_manager"]
INGEST_SIDE = [("chunking", "strategy"), ("processing", "html_to_markdown", "enabled"),
               ("processing", "categorization", "enabled"), ("stemming", "enabled")]
def get(d, path):
    for k in path:
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d
for path in INGEST_SIDE:
    a, r = get(arm, path), get(dm, path)
    if a is not None and a != r:
        print(f"ingest-side key {'.'.join(path)} differs: arm={a!r} stack={r!r}", file=sys.stderr)
        sys.exit(3)
changed = []
hr_arm = get(arm, ("retrievers", "hierarchical_rerank")) or {}
hr = dm.setdefault("retrievers", {}).setdefault("hierarchical_rerank", {})
for key in ("enabled", "candidate_pool_size", "num_documents_to_retrieve"):
    if key in hr_arm and hr.get(key) != hr_arm[key]:
        changed.append(f"retrievers.hierarchical_rerank.{key}: {hr.get(key)!r} -> {hr_arm[key]!r}")
        hr[key] = hr_arm[key]
with open(os.environ["FM_RENDERED"], "w") as f:
    yaml.safe_dump(rendered, f, sort_keys=False)
print("\n".join(changed) if changed else "no retrieval key differs from the rendered config")
EOF

fm_log "re-seeding static_config for $STACK"
"$FM_DOCKER" compose -f "$STACK_DIR/compose.yaml" up --force-recreate config-seed
"$FM_DOCKER" rm -f "benchmarking-$STACK" >/dev/null 2>&1 || true
"$FM_DOCKER" compose -f "$STACK_DIR/compose.yaml" up --no-deps -d benchmark
fm_require_pinned_corpus "$STACK"
fm_ledger_append "$(printf '{"arm":"%s","kind":"ragas-start","stack":"%s","config":"%s","started":"%s","rerun":true,"reseed":true}' "$ARM" "$STACK" "$YAML" "$(fm_now)")"
fm_log "arm $ARM running on $STACK after re-seed; follow: $FM_DOCKER logs -f benchmarking-$STACK"

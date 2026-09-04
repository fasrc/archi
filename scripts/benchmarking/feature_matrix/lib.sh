#!/usr/bin/env bash
# Shared helpers for the #396 feature-matrix runbook wrappers. Source, do not run.
#
# Every wrapper is one step of docs/docs/proposals/feature-matrix-campaign-2026.md §5 and
# refuses (exit 2) before it touches anything when a precondition fails. The knobs below
# are environment variables so the self-test can point them at stubs and a sandbox:
#   FM_OUT      where `archi evaluate` writes artifacts — the arm YAML's out_dir resolved
#               from the cwd (default: $PWD/bench_out/feature_matrix)
#   ARCHI_DIR   the archi deployments root (default: ~/.archi), same as the archi CLI
#   FM_DOCKER   the docker binary (default: docker)
#   FM_ARCHI    the archi CLI (default: archi)
#   FM_PYTHON   the python used for the YAML/JSON edits (default: python3)
set -euo pipefail

FM_OUT="${FM_OUT:-$PWD/bench_out/feature_matrix}"
ARCHI_DIR="${ARCHI_DIR:-$HOME/.archi}"
FM_DOCKER="${FM_DOCKER:-docker}"
FM_ARCHI="${FM_ARCHI:-archi}"
FM_PYTHON="${FM_PYTHON:-python3}"

fm_die() { printf 'feature_matrix: %s\n' "$*" >&2; exit 2; }
fm_log() { printf '==> %s\n' "$*"; }

# Arm labels are two digits with an optional letter: 00, 05a. Anything else is refused
# before it becomes a deployment name (the name reaches `archi delete --force` paths).
fm_require_arm() { [[ "${1:-}" =~ ^[0-9]{2}[a-z]?$ ]] || fm_die "bad arm label '${1:-}' (want 00, 03, 05a ...)"; }

fm_stack_dir() { printf '%s/archi-%s\n' "$ARCHI_DIR" "$1"; }
fm_pin_file()  { printf '%s/corpus-pin-%s\n' "$FM_OUT" "$1"; }
fm_ledger()    { printf '%s/ledger.json\n' "$FM_OUT"; }

fm_container_state() { # $1 = container name → running | exited | absent | ...
  "$FM_DOCKER" inspect "$1" --format '{{.State.Status}}' 2>/dev/null || printf 'absent\n'
}

fm_require_stack_up() { # $1 = stack name; Postgres and the data-manager must stay up (a restart re-ingests)
  local c st
  for c in "postgres-$1" "data-manager-$1"; do
    st="$(fm_container_state "$c")"
    [ "$st" = "running" ] || fm_die "$c is $st — it must stay up; restarting it re-ingests and voids the arm"
  done
}

# The corpus fingerprint: the same query swap_arm.sh and the harness's corpus_fingerprint
# use — every live document's identity and content hash, in a fixed order, md5'd.
fm_fingerprint() { # $1 = stack name
  "$FM_DOCKER" exec "postgres-$1" psql -U archi -d archi-db -tAc \
    "select md5(string_agg(coalesce(url,file_path,'')||':'||coalesce(resource_hash,''), E'\n' order by coalesce(url,file_path,''), resource_hash)) from documents where is_deleted is not true;" \
    | tr -d '[:space:]'
}

fm_require_pinned_corpus() { # $1 = stack name → refuses unless the fingerprint equals the recorded pin
  local pin_file pin now
  pin_file="$(fm_pin_file "$1")"
  [ -f "$pin_file" ] || fm_die "no corpus pin for $1 at $pin_file — archive run 1 first (archive_run.sh writes it)"
  pin="$(tr -d '[:space:]' < "$pin_file")"
  now="$(fm_fingerprint "$1")"
  [ "$now" = "$pin" ] || fm_die "corpus fingerprint $now != pin $pin for $1 — the arm would be void"
  fm_log "corpus fingerprint matches pin $pin"
}

fm_ledger_append() { # $1 = JSON object (one line); appends to the ledger array, creating it
  local ledger; ledger="$(fm_ledger)"
  mkdir -p "$(dirname "$ledger")"
  FM_LEDGER="$ledger" FM_ENTRY="$1" "$FM_PYTHON" - <<'EOF'
import json, os
path, entry = os.environ["FM_LEDGER"], json.loads(os.environ["FM_ENTRY"])
rows = json.load(open(path)) if os.path.exists(path) else []
rows.append(entry)
with open(path, "w") as f:
    json.dump(rows, f, indent=1)
EOF
}

fm_now() { date -u +%Y-%m-%dT%H:%M:%SZ; }

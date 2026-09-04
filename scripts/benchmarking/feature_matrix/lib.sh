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

# An arm YAML self-identifies through its `name: fm-<arm>`; the label the operator typed
# must agree with it, or a run, a re-seed, or an archive row is filed under the wrong arm.
fm_require_arm_yaml() { # $1 = arm label, $2 = arm YAML
  [ -n "${2:-}" ] && [ -f "$2" ] || fm_die "arm config not found: '${2:-}' (run from ~/Projects/archi so config/... resolves)"
  local name
  name="$(FM_Y="$2" "$FM_PYTHON" -c 'import os,yaml; print(yaml.safe_load(open(os.environ["FM_Y"])).get("name",""))')"
  [ "$name" = "fm-$1" ] || fm_die "arm label $1 does not match $2 (its name is '$name', expected 'fm-$1')"
}

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

# The corpus fingerprint, computed EXACTLY as the benchmark artifact records it: the
# harness's CORPUS_STATE_QUERY (documents, chunks and parent nodes — the retrievable
# state, not just the document list) hashed by src.utils.benchmark_provenance
# .corpus_fingerprint (sorted (key, value) rows, sha256). The pin archive_run.sh writes
# comes from the artifact, so the live check must speak the same digest or every re-run
# would refuse — or, worse, certify a stack whose chunks drifted under an unchanged
# document list. The snippet runs inside the stack's data-manager container, which
# carries the same source tree and the Postgres connection env; the query text is read
# from the harness source (not re-typed here) so the two cannot drift apart.
fm_fingerprint() { # $1 = stack name
  "$FM_DOCKER" exec -w /root/archi "data-manager-$1" python -c '
import ast, pathlib
src = pathlib.Path("src/bin/service_benchmark.py").read_text()
query = next(node.value.value for node in ast.parse(src).body
             if isinstance(node, ast.Assign)
             and any(getattr(t, "id", None) == "CORPUS_STATE_QUERY" for t in node.targets))
from src.utils.benchmark_provenance import corpus_fingerprint
from src.utils.postgres_service_factory import PostgresServiceFactory
print(corpus_fingerprint(PostgresServiceFactory.from_env().connection_pool.execute(query)))
' | tr -d '[:space:]'
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

# The factor keys every arm YAML states (plan §3). A stack "is on" an arm when its rendered
# config agrees with the arm YAML on all of them.
fm_verify_stack_matches_arm() { # $1 = stack name, $2 = arm YAML → refuses on any differing key
  local rendered; rendered="$(fm_stack_dir "$1")/configs/config.yaml"
  [ -f "$rendered" ] || fm_die "no rendered config at $rendered"
  FM_ARM_YAML="$2" FM_RENDERED="$rendered" "$FM_PYTHON" - <<'EOF' || fm_die "stack $1 is not on the arm described by $2 (see above); re-seed or pick the right --stack"
import os, sys, yaml
arm = yaml.safe_load(open(os.environ["FM_ARM_YAML"]))["data_manager"]
dm = yaml.safe_load(open(os.environ["FM_RENDERED"]))["data_manager"]
KEYS = [("chunking", "strategy"), ("processing", "html_to_markdown", "enabled"),
        ("processing", "categorization", "enabled"), ("stemming", "enabled"),
        ("retrievers", "hierarchical_rerank", "enabled"),
        ("retrievers", "hierarchical_rerank", "candidate_pool_size"),
        ("retrievers", "hierarchical_rerank", "num_documents_to_retrieve")]
def get(d, path):
    for k in path:
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d
bad = [(".".join(p), get(arm, p), get(dm, p)) for p in KEYS if get(arm, p) is not None and get(arm, p) != get(dm, p)]
for key, a, r in bad:
    print(f"factor {key}: arm={a!r} stack={r!r}", file=sys.stderr)
sys.exit(1 if bad else 0)
EOF
}

fm_sha256() { sha256sum "$1" | cut -d' ' -f1; }

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

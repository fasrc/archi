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
FM_GIT="${FM_GIT:-git}"

fm_require_run_number() { [[ "${1:-}" =~ ^[1-9][0-9]*$ ]] || fm_die "run number must be a positive integer, got '${1:-}'"; }

fm_die() { printf 'feature_matrix: %s\n' "$*" >&2; exit 2; }
fm_log() { printf '==> %s\n' "$*"; }

# Arm labels are two digits with an optional letter: 00, 05a. Anything else is refused
# before it becomes a deployment name (the name reaches `archi delete --force` paths).
fm_require_arm() { [[ "${1:-}" =~ ^[0-9]{2}[a-z]?$ ]] || fm_die "bad arm label '${1:-}' (want 00, 03, 05a ...)"; }

# The factor keys every arm YAML must state (plan §3). The checks below fail CLOSED: a key
# missing from the arm YAML or from the configuration it is compared with is a mismatch,
# never "don't care" — a sparse YAML would otherwise certify the wrong stack or artifact.
FM_FACTOR_KEYS='chunking.strategy processing.html_to_markdown.enabled processing.categorization.enabled stemming.enabled retrievers.hierarchical_rerank.enabled retrievers.hierarchical_rerank.candidate_pool_size retrievers.hierarchical_rerank.num_documents_to_retrieve'

# An arm YAML self-identifies through its `name: fm-<arm>`; the label the operator typed
# must agree with it, or a run, a re-seed, or an archive row is filed under the wrong arm.
# It must also state every factor key, so the arm proofs below have something to compare.
fm_require_arm_yaml() { # $1 = arm label, $2 = arm YAML
  [ -n "${2:-}" ] && [ -f "$2" ] || fm_die "arm config not found: '${2:-}' (run from ~/Projects/archi so config/... resolves)"
  FM_Y="$2" FM_ARM="$1" FM_KEYS="$FM_FACTOR_KEYS" "$FM_PYTHON" - <<'EOF' || fm_die "arm YAML $2 is not a complete arm $1 config (see above)"
import os, sys, yaml
cfg = yaml.safe_load(open(os.environ["FM_Y"])) or {}
name, arm = cfg.get("name", ""), os.environ["FM_ARM"]
if name != f"fm-{arm}":
    sys.exit(f"arm label {arm} does not match the YAML (its name is '{name}', expected 'fm-{arm}')")
dm = cfg.get("data_manager") or {}
def get(m, path):
    for k in path.split("."):
        if not isinstance(m, dict) or k not in m:
            return None
        m = m[k]
    return m
missing = [k for k in os.environ["FM_KEYS"].split() if get(dm, k) is None]
if missing:
    sys.exit("arm YAML lacks factor key(s): " + ", ".join(missing))
EOF
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
import ast, pathlib, sys
src = pathlib.Path("src/bin/service_benchmark.py").read_text()
queries = [node.value.value for node in ast.parse(src).body
           if isinstance(node, ast.Assign)
           and any(getattr(t, "id", None) == "CORPUS_STATE_QUERY" for t in node.targets)]
if not queries:
    sys.exit("this image carries a harness with no CORPUS_STATE_QUERY (it predates the "
             "corpus fingerprint); rebuild the stack from the campaign SHA")
from src.utils.benchmark_provenance import corpus_fingerprint
from src.utils.postgres_service_factory import PostgresServiceFactory
print(corpus_fingerprint(PostgresServiceFactory.from_env().connection_pool.execute(queries[0])))
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

# A stack "is on" an arm when its rendered config agrees with the arm YAML on EVERY
# factor key; a key absent on either side is a mismatch (fail closed).
fm_verify_stack_matches_arm() { # $1 = stack name, $2 = arm YAML → refuses on any differing or missing key
  local rendered; rendered="$(fm_stack_dir "$1")/configs/config.yaml"
  [ -f "$rendered" ] || fm_die "no rendered config at $rendered"
  FM_ARM_YAML="$2" FM_RENDERED="$rendered" FM_KEYS="$FM_FACTOR_KEYS" "$FM_PYTHON" - <<'EOF' || fm_die "stack $1 is not on the arm described by $2 (see above); re-seed or pick the right --stack"
import os, sys, yaml
arm = (yaml.safe_load(open(os.environ["FM_ARM_YAML"])) or {}).get("data_manager") or {}
dm = (yaml.safe_load(open(os.environ["FM_RENDERED"])) or {}).get("data_manager") or {}
def get(m, path):
    for k in path.split("."):
        if not isinstance(m, dict) or k not in m:
            return None
        m = m[k]
    return m
bad = [(k, get(arm, k), get(dm, k)) for k in os.environ["FM_KEYS"].split()
       if get(arm, k) is None or get(dm, k) is None or get(arm, k) != get(dm, k)]
for key, a, r in bad:
    print(f"factor {key}: arm={a!r} stack={r!r}", file=sys.stderr)
sys.exit(1 if bad else 0)
EOF
}

fm_sha256() { sha256sum "$1" | cut -d' ' -f1; }

# --- the campaign lock -------------------------------------------------------------------
# The pre-registration fixes every input that is NOT an arm factor: the bank, the anchors,
# the prompt, the sources list, the SUT and the judge settings (plan §2). lock_campaign.sh
# hashes them once from the baseline arm YAML into $FM_OUT/campaign.lock (plan §6 step 3).
# Every wrapper then refuses an arm YAML, dataset, profile or spec whose content differs
# from the lock, so acceptance depends on content, never on which file an operator named.
fm_lock_file() { printf '%s/campaign.lock\n' "$FM_OUT"; }

# Prints the fixed factors an arm YAML pins, as JSON: the sha256 of each file it names and
# the SUT/judge/metric settings. Paths resolve from the cwd, like `archi evaluate` does.
fm_fixed_factors_json() { # $1 = arm YAML
  FM_Y="$1" "$FM_PYTHON" - <<'EOF'
import hashlib, json, os, sys, yaml
cfg = yaml.safe_load(open(os.environ["FM_Y"])) or {}
b = (cfg.get("services") or {}).get("benchmarking") or {}
rs = (b.get("mode_settings") or {}).get("ragas_settings") or {}
dm = cfg.get("data_manager") or {}
provider = b.get("provider")
prov_cfg = (((cfg.get("services") or {}).get("chat_app") or {}).get("providers") or {}).get(provider) or {}
files = {"bank": b.get("queries_path"), "anchors": (b.get("anchors") or {}).get("path"), "prompt": b.get("agent_md_file")}
for i, p in enumerate(((dm.get("sources") or {}).get("links") or {}).get("input_lists") or []):
    files[f"sources[{i}]"] = p
def sha(path):
    if not path or not os.path.isfile(path):
        sys.exit(f"pinned input not found: {path!r} (run from ~/Projects/archi so config/... resolves)")
    return hashlib.sha256(open(path, "rb").read()).hexdigest()
out = {"files": {k: {"path": v, "sha256": sha(v)} for k, v in files.items()},
       "values": {"sut.agent_class": b.get("agent_class"), "sut.provider": provider, "sut.model": b.get("model"), "modes": b.get("modes"),
                  "judge.provider": rs.get("evaluator_provider"), "judge.model": rs.get("evaluator_model"),
                  "metrics": rs.get("enabled_metrics"), "ragas.embedding_model": rs.get("embedding_model"),
                  "embedding_name": dm.get("embedding_name"),
                  "sut.base_url": prov_cfg.get("base_url"), "sut.extra_kwargs": prov_cfg.get("extra_kwargs")}}
print(json.dumps(out, sort_keys=True))
EOF
}

fm_require_lock() { # $1 = arm YAML → refuses unless its fixed factors equal the campaign lock's
  local lock; lock="$(fm_lock_file)"
  [ -f "$lock" ] || fm_die "no campaign lock at $lock — run lock_campaign.sh <00-baseline.yaml> first (plan §6 step 3)"
  FM_LOCK="$lock" FM_NOW="$(fm_fixed_factors_json "$1")" "$FM_PYTHON" - <<'EOF' || fm_die "$1 does not match the campaign lock (see above); the pre-registration pins these inputs"
import json, os, sys
lock, now = json.load(open(os.environ["FM_LOCK"])), json.loads(os.environ["FM_NOW"])
bad = []
for key, want in lock["files"].items():
    got = now["files"].get(key)
    if got is None or got["sha256"] != want["sha256"]:
        bad.append(f"{key}: locked sha256 {want['sha256'][:12]} ({want['path']}), arm names {got and got['path']!r} sha256 {got and got['sha256'][:12]}")
for key in set(now["files"]) - set(lock["files"]):
    bad.append(f"{key}: not in the lock (extra source list?)")
for key, want in lock["values"].items():
    if now["values"].get(key) != want:
        bad.append(f"{key}: locked {want!r}, arm has {now['values'].get(key)!r}")
for line in bad:
    print("fixed factor " + line, file=sys.stderr)
sys.exit(1 if bad else 0)
EOF
}

fm_lock_sha() { fm_sha256 "$(fm_lock_file)"; }

# The code the campaign runs is locked too: `archi evaluate` builds the stack images from
# the cwd's tree and `archi eval qa` runs it in-process, so a checkout that advanced or
# carries uncommitted source changes after the lock would run different code while every
# wrapper still succeeded. HEAD must equal the locked code_sha and src/, scripts/, deploy/
# must carry no tracked modification.
fm_require_code_lock() {
  local want have dirty
  want="$(fm_lock_field code_sha)"
  have="$("$FM_GIT" rev-parse HEAD 2>/dev/null || echo unknown)"
  [ -n "$want" ] && [ "$have" = "$want" ] || fm_die "checkout HEAD $have is not the locked campaign code $want (git checkout it, or re-lock deliberately with lock_campaign.sh --relock)"
  dirty="$("$FM_GIT" status --porcelain --untracked-files=no -- src scripts deploy 2>/dev/null || true)"
  [ -z "$dirty" ] || fm_die "uncommitted source changes would run unlocked code:
$dirty"
}
fm_code_sha() { "$FM_GIT" rev-parse HEAD 2>/dev/null || echo unknown; }

fm_lock_field() { # $1 = dotted key inside the lock, e.g. qa.dataset_sha256
  FM_LOCK="$(fm_lock_file)" FM_KEY="$1" "$FM_PYTHON" -c '
import json, os
v = json.load(open(os.environ["FM_LOCK"]))
for k in os.environ["FM_KEY"].split("."):
    v = v.get(k) if isinstance(v, dict) else None
print("" if v is None else v)'
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

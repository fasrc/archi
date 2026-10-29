#!/usr/bin/env bash
# Show the trial's progress: queued/running jobs, and per judge and bundle whether the
# scores file exists, how many (row, metric) pairs the checkpoint holds, or nothing yet.
#
#   bash scripts/status.sh            # from the package root, on a login node
set -euo pipefail

PKG="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

if command -v squeue >/dev/null 2>&1; then
  echo "== Slurm jobs (archi-judge-trial-*) for $USER"
  squeue -u "$USER" --name="$(ls "$PKG"/judges/*.env 2>/dev/null | xargs -n1 basename | sed 's/\.env$/archi-judge-trial-&/; s/\.env$//' | sed 's/^/archi-judge-trial-/' | paste -sd, -)" \
    -o "%.10i %.28j %.9P %.8T %.10M %.10l %.6D %R" 2>/dev/null || squeue -u "$USER" -o "%.10i %.28j %.9P %.8T %.10M %.10l %.6D %R"
  echo
fi

pairs_expected() { # $1 = bundle folder: rows x metrics (upper bound; some rows are ineligible)
  python3 - "$1" <<'PY'
import json, sys
from pathlib import Path
b = Path(sys.argv[1])
m = json.loads((b / "manifest.json").read_text())
print(m["row_count"] * len(m["enabled_metrics"]))
PY
}

echo "== Bundles"
for bundle in "$PKG"/bundles/*/; do
  bundle="${bundle%/}"
  name="$(basename "$bundle")"
  rows="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["row_count"])' "$bundle/manifest.json")"
  printf '  %-60s %4s rows\n' "$name" "$rows"
done
echo

for env_file in "$PKG"/judges/*.env; do
  judge="$(basename "$env_file" .env)"
  echo "== $judge"
  out="$PKG/scores/$judge"
  for bundle in "$PKG"/bundles/*/; do
    bundle="${bundle%/}"
    name="$(basename "$bundle")"
    if [ -f "$out/$name.judge_scores.json" ]; then
      agg="$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(", ".join(f"{k.replace(chr(97)+chr(103)+chr(103)+chr(114)+chr(101)+chr(103)+chr(97)+chr(116)+chr(101)+chr(95),"")}={v:.3f}" for k,v in sorted(d["aggregates"].items()) if k.startswith("aggregate_") and isinstance(v,(int,float))))' "$out/$name.judge_scores.json" 2>/dev/null || true)"
      printf '  %-60s SCORED  %s\n' "$name" "$agg"
    else
      partial="$(ls "$out/$name".scores.*.partial.jsonl 2>/dev/null | head -1 || true)"
      if [ -n "$partial" ]; then
        done_pairs="$(wc -l < "$partial")"
        printf '  %-60s partial %s/%s pairs\n' "$name" "$done_pairs" "$(pairs_expected "$bundle")"
      else
        printf '  %-60s pending\n' "$name"
      fi
    fi
  done
  log="$(ls -t "$PKG"/logs/archi-judge-trial-"$judge"-*.out 2>/dev/null | head -1 || true)"
  [ -z "$log" ] || { echo "  last log: $log"; tail -n 2 "$log" | sed 's/^/    /'; }
  echo
done

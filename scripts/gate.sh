#!/usr/bin/env bash
# scripts/gate.sh — archi's quality gate, in CI order. SINGLE SOURCE.
#
# Project-OWNED config (not harness machinery). PROMPT.md, the pre-commit hook
# (hooks/pre-commit), and CI (.github/workflows/ci.yml) all invoke this one file —
# the gate command lives here and nowhere else, so the three can never drift.
#
# Order mirrors CI: FORMAT (black, isort — writers), then TEST with PATCH
# COVERAGE. Coverage is gated on the lines this branch changes vs the base branch
# (diff coverage via diff-cover), NOT a whole-package floor: a global floor on an
# under-covered brownfield package (vectorstore baseline ~55%) blocks every commit,
# even ones touching no vectorstore code. Diff coverage holds NEW code to standard
# and lets unrelated commits through; the review gate backstops the rest.
#
# RELEASE PRs (dev→main): CI compares vs origin/dev (not main) and sets
# GATE_COVERAGE_ADVISORY=1, so patch coverage is reported but does not fail. A
# dev→main merge re-measured vs main would cover the whole accumulated release
# (incl. never-covered legacy code) and can't fairly hit 80%; dev was already
# gated per-commit on the way in.
#
# FORMAT SCOPE: archi's HEAD is not black-clean and CI runs black/isort as advisory
# (continue-on-error), so formatting the WHOLE tree (`black .`) would rewrite ~150
# unrelated files into the working tree every run. Instead we format only the .py
# files this turn touched (changed-vs-HEAD plus new untracked) — keeping the turn's
# own output clean without churning the rest of the repo.
#
# `python -m pytest` (not bare `pytest`) so the repo root is on sys.path and the
# `src` package imports resolve, matching CI.

set -euo pipefail

# Files this turn touched: modified/added vs HEAD (excluding deletions) + new
# untracked .py files. NUL-delimited to survive odd paths.
mapfile -d '' -t changed < <(
  { git diff --name-only -z --diff-filter=d HEAD -- '*.py'
    git ls-files --others --exclude-standard -z -- '*.py'
  } | sort -zu
)

if [ ${#changed[@]} -gt 0 ]; then
  black "${changed[@]}"
  isort "${changed[@]}"
fi

# A change is "formatting-only" (for patch-coverage purposes) when every .py file
# that differs from the base branch is EXACTLY what `isort` then `black` produce
# from that file's base-branch version — i.e. the diff is pure reformatting that
# introduces no new or altered logic. Such a diff cannot meaningfully meet a
# coverage bar (it adds no testable code), so patch coverage is reported but not
# enforced, mirroring the GATE_COVERAGE_ADVISORY release path. Any real content
# change, a brand-new file, or an untracked .py makes the run NON-formatting-only.
# This is what lets a one-shot `black`/`isort` tree normalization land without the
# reformatted (mostly uncovered) legacy lines failing diff coverage.
_diff_is_formatting_only() {
  local base="$1" f base_blob reformatted
  # Untracked .py = new, untested code -> never formatting-only.
  [ -n "$(git ls-files --others --exclude-standard -- '*.py')" ] && return 1
  local -a py
  mapfile -t py < <(git diff --name-only --diff-filter=d "$base" -- '*.py' | sort -u)
  [ ${#py[@]} -eq 0 ] && return 1
  for f in "${py[@]}"; do
    [ -f "$f" ] || return 1
    base_blob=$(git show "$base:$f" 2>/dev/null) || return 1  # absent in base -> new file
    reformatted=$(printf '%s' "$base_blob" | isort --profile black -q - 2>/dev/null \
                  | black -q - 2>/dev/null) || return 1
    [ "$reformatted" = "$(cat "$f")" ] || return 1
  done
  return 0
}

# Full coverage report, then PATCH coverage against the base branch.
BASE="${DIFF_COVER_BASE:-origin/dev}"
python -m pytest tests/unit/ --cov=src --cov-report=xml --cov-report=term-missing

if git rev-parse --verify --quiet "$BASE" >/dev/null; then
  advisory="${GATE_COVERAGE_ADVISORY:-}"
  advisory_reason="release PR"
  # Release PRs (dev→main): CI sets GATE_COVERAGE_ADVISORY=1 because re-measuring the
  # whole accumulated release vs main can't fairly hit 80%; dev was gated per-commit.
  # A formatting-only diff gets the same treatment for the same reason: there is no
  # new logic to cover. Detect it only when not already advisory (saves the work).
  if [ "$advisory" != "1" ] && _diff_is_formatting_only "$BASE"; then
    advisory="1"
    advisory_reason="formatting-only diff (every changed .py equals isort+black of its base version)"
  fi
  if [ "$advisory" = "1" ]; then
    echo "gate: patch coverage is ADVISORY for this run (${advisory_reason}) — reporting vs '$BASE', not failing" >&2
    diff-cover coverage.xml --compare-branch="$BASE" || true
  else
    diff-cover coverage.xml --compare-branch="$BASE" --fail-under=80
  fi
else
  echo "gate: base ref '$BASE' not available — skipping patch-coverage check (set DIFF_COVER_BASE)" >&2
fi

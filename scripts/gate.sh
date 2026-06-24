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
# FORMAT SCOPE: archi's HEAD is not black-clean and CI runs black/isort as advisory
# (continue-on-error), so formatting the WHOLE tree (`black .`) would rewrite ~150
# unrelated files into the working tree every run. Instead we format only the .py
# files this branch touched, scoped two ways so the gate is meaningful both locally
# and in CI:
#   - locally (pre-commit): changed-vs-HEAD + new untracked, written in place;
#   - in CI: a fresh checkout has an empty changed-vs-HEAD, so we ALSO include the
#     branch's changes vs the base branch and run black/isort in --check mode, so
#     unformatted Python in the diff fails the build instead of slipping through.
#
# `python -m pytest` (not bare `pytest`) so the repo root is on sys.path and the
# `src` package imports resolve, matching CI.

set -euo pipefail

BASE="${DIFF_COVER_BASE:-origin/dev}"

# Files this branch touched: working-tree changes vs HEAD (excluding deletions),
# new untracked .py files, and — so CI's clean checkout still sees them — the
# branch's changes vs the base branch. NUL-delimited to survive odd paths.
collect_changed() {
  git diff --name-only -z --diff-filter=d HEAD -- '*.py'
  git ls-files --others --exclude-standard -z -- '*.py'
  if git rev-parse --verify --quiet "$BASE" >/dev/null; then
    git diff --name-only -z --diff-filter=d "$BASE"...HEAD -- '*.py'
  fi
}
mapfile -d '' -t changed < <(collect_changed | sort -zu)

if [ ${#changed[@]} -gt 0 ]; then
  if [ -n "${CI:-}" ]; then
    # CI: do not rewrite; fail on any unformatted file in the diff.
    black --check --diff "${changed[@]}"
    isort --check-only --diff "${changed[@]}"
  else
    black "${changed[@]}"
    isort "${changed[@]}"
  fi
fi

# Full coverage report, then PATCH coverage against the base branch.
python -m pytest tests/unit/ --cov=src --cov-report=xml --cov-report=term-missing

if git rev-parse --verify --quiet "$BASE" >/dev/null; then
  diff-cover coverage.xml --compare-branch="$BASE" --fail-under=80
else
  echo "gate: base ref '$BASE' not available — skipping patch-coverage check (set DIFF_COVER_BASE)" >&2
fi

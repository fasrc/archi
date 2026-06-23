#!/usr/bin/env bash
# scripts/gate.sh — archi's quality gate, in CI order. SINGLE SOURCE.
#
# Project-OWNED config (not harness machinery). PROMPT.md, the pre-commit hook
# (hooks/pre-commit), and CI (.github/workflows/ci.yml) all invoke this one file —
# the gate command lives here and nowhere else, so the three can never drift.
#
# Order mirrors CI: FORMAT (black, isort — writers), then TEST with scoped
# COVERAGE. Coverage is scoped to src/data_manager/vectorstore — the package the
# add-title-aware-retrieval feature touches — at a 60% floor: high enough to force
# real tests on the feature's logic, low enough that the package's Postgres I/O
# paths don't block every commit. The review gate backstops changes outside this
# scoped path.
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

python -m pytest tests/unit/ --cov=src/data_manager/vectorstore --cov-report=term-missing --cov-fail-under=60

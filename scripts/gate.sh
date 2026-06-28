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
# FORMAT SCOPE + ENFORCEMENT: the tree was normalized black/isort-clean (#69), so
# formatting is now ENFORCED, not advisory. In CI (`$CI` set) the gate runs black/isort
# in --check mode and FAILS on any misformat; local pre-commit keeps WRITER mode (formats
# in place) so the loop's output stays clean with no manual format step. The file set is
# the .py this turn touched — changed-vs-HEAD + new untracked + changed-vs-base
# (`"$BASE"...HEAD`). That last source matters in CI: on a fresh checkout HEAD already ==
# the PR tip, so changed-vs-HEAD is empty — without the base diff the check would be a
# no-op (the original bug, #34). A companion whole-tree check lives in the pr-preview
# `lint` job; this one is diff-scoped and is the single source the loop also runs.
#
# `python -m pytest` (not bare `pytest`) so the repo root is on sys.path and the
# `src` package imports resolve, matching CI.

set -euo pipefail

BASE="${DIFF_COVER_BASE:-origin/dev}"

# Enforcement scope: the dirs normalized black/isort-clean in #69 — src/ (incl src/bin),
# tests/, scripts/. Kept identical to the pr-preview `lint` job's check scope and to #34's
# PR A, so "what gets normalized", "what the gate enforces", and "what the lint job checks"
# can never drift. setup.py and other out-of-scope tracked .py are intentionally excluded
# (they were not normalized, and enforcing them would block edits on legacy dirt — the very
# problem #34 fixes). git pathspec `src/*.py` matches .py at any depth under src/.
_FMT_SCOPE=('src/*.py' 'tests/*.py' 'scripts/*.py')

# The .py files this run is responsible for: modified/added vs HEAD (excluding deletions,
# the local pre-commit idiom) + new untracked + changed-vs-base (`"$BASE"...HEAD`, so a
# fresh CI checkout — where HEAD already == the PR tip — still sees the PR's files instead
# of an empty set). NUL-delimited to survive odd paths.
_changed_py() {
  { git diff --name-only -z --diff-filter=d HEAD -- "${_FMT_SCOPE[@]}"
    git ls-files --others --exclude-standard -z -- "${_FMT_SCOPE[@]}"
    if git rev-parse --verify --quiet "$BASE" >/dev/null; then
      git diff --name-only -z --diff-filter=d "$BASE"...HEAD -- "${_FMT_SCOPE[@]}"
    fi
  } | sort -zu
}

# Format step. CI (`$CI` set): CHECK ONLY and FAIL on any misformat — formatting is
# enforced, not silently rewritten. Local pre-commit: rewrite in place (writer), keeping
# the loop's output clean. isort/black are pinned (see ci.yml) so the two modes agree
# byte-for-byte.
_format_step() {
  [ "$#" -eq 0 ] && return 0
  # `&&` so a black failure propagates as this function's own return code (don't let a
  # passing isort mask it); under the script's set -e a non-zero return aborts the gate.
  if [ -n "${CI:-}" ]; then
    black --check "$@" && isort --check-only "$@"
  else
    black "$@" && isort "$@"
  fi
}

mapfile -d '' -t changed < <(_changed_py)
if [ ${#changed[@]} -gt 0 ]; then
  _format_step "${changed[@]}"
fi

# A change is "formatting-only" (for patch-coverage purposes) when the ENTIRE diff
# vs the base branch is pure reformatting: every changed path is a MODIFIED .py file
# whose working-tree content already equals what `isort` then `black` produce from
# that file's base version — no added/deleted .py, no non-.py path (workflow, shell,
# template), and no untracked .py. Such a diff cannot meaningfully meet a coverage bar
# (it adds no testable code), so patch coverage is reported but not enforced, mirroring
# the GATE_COVERAGE_ADVISORY release path. This is what lets a one-shot `black`/`isort`
# tree normalization land without its reformatted (mostly uncovered) legacy lines
# failing diff coverage; anything else falls through to enforced coverage.
#
# Three correctness anchors (Codex review on #68):
#   - WHOLE diff: scan every changed path and status, not just modified .py files, so a
#     bundled deletion or non-.py edit cannot ride the advisory escape.
#   - MERGE BASE: compare from the fork point (`git merge-base`), exactly as diff-cover's
#     `...` range does, so a branch behind origin/dev is not judged against files that
#     only moved on origin/dev (which would wrongly block a formatting-only branch).
#   - REPO ROOT: run every git query with `git -C "$root"` so a call from a subdirectory
#     (a manual run; git already runs the pre-commit hook and CI from the root) still
#     scans the whole tree instead of a cwd-relative slice.
_diff_is_formatting_only() {
  local base="$1" root mb status path base_blob reformatted current saw=0
  root=$(git rev-parse --show-toplevel) || return 1
  # Untracked .py = new, untested code -> never formatting-only.
  [ -n "$(git -C "$root" ls-files --others --exclude-standard -- '*.py')" ] && return 1
  mb=$(git -C "$root" merge-base "$base" HEAD) || return 1
  while IFS=$'\t' read -r status path; do
    [ -z "$status" ] && continue
    saw=1
    [ "$status" = "M" ] || return 1                    # added/deleted/renamed/etc.
    case "$path" in *.py) ;; *) return 1 ;; esac       # any non-.py path disqualifies
    base_blob=$(git -C "$root" show "$mb:$path" 2>/dev/null) || return 1
    reformatted=$(printf '%s' "$base_blob" | isort --profile black -q - 2>/dev/null \
                  | black -q - 2>/dev/null) || return 1
    current=$(cat -- "$root/$path") || return 1
    [ "$reformatted" = "$current" ] || return 1
  done < <(git -C "$root" diff --name-status "$mb")
  [ "$saw" -eq 1 ] || return 1                          # empty diff is not formatting-only
  return 0
}

# Full coverage report, then PATCH coverage against the base branch ($BASE, set above).
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

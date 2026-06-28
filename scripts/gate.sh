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
# FORMAT SCOPE + ENFORCEMENT: the tree was normalized black/isort-clean (#69), so formatting
# is now ENFORCED, not advisory. In CI (`$CI` set) the gate asserts the WHOLE normalized scope
# (src/ incl src/bin, tests/, scripts/) is black/isort-clean and FAILS on any misformat. That
# check is DIFF-INDEPENDENT on purpose: an `on: push` build to dev has HEAD == origin/dev (empty
# diff) and a fresh PR checkout has an empty changed-vs-HEAD, so a diff-scoped check would be a
# no-op on exactly those events (the original #34 bug, and Codex #71). Local pre-commit keeps
# WRITER mode, formatting only the in-scope .py this turn touched (changed-vs-HEAD + new
# untracked) so the loop's output stays clean without churning the tree. setup.py and other
# out-of-scope tracked .py are intentionally excluded (never normalized; enforcing them would
# block edits on legacy dirt — the problem #34 fixes). The pr-preview `lint` job runs the same
# scoped check on PRs.
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
_FMT_SCOPE=('src/*.py' 'tests/*.py' 'scripts/*.py')   # git pathspecs — local writer set
_FMT_DIRS=(src tests scripts)                          # dir args — CI whole-scope check

# Local pre-commit writer set: modified/added vs HEAD (deletions excluded via diff-filter=d)
# + new untracked, within scope. NUL-delimited to survive odd paths. Deliberately NOT a
# base-branch (`"$BASE"...HEAD`) diff: that reflects committed history, so a file deleted or
# renamed in the staged-but-uncommitted change still shows up and the writer would call black
# on a path that no longer exists, blocking the commit (Codex #71). CI does not use this set —
# see _check_format_scope.
_changed_py() {
  { git diff --name-only -z --diff-filter=d HEAD -- "${_FMT_SCOPE[@]}"
    git ls-files --others --exclude-standard -z -- "${_FMT_SCOPE[@]}"
  } | sort -zu
}

# CI enforcement (`$CI` set): assert the WHOLE normalized scope is black/isort-clean and FAIL
# on any misformat. Diff-independent (see header) so `on: push` builds and fresh checkouts are
# covered, not just PR diffs. Dir args, so black/isort skip gitignored paths, mirroring the
# pr-preview `lint` job. `&&` so a black failure isn't masked by a passing isort.
_check_format_scope() {
  local -a dirs=()
  local d
  for d in "${_FMT_DIRS[@]}"; do [ -d "$d" ] && dirs+=("$d"); done
  [ ${#dirs[@]} -eq 0 ] && return 0
  black --check "${dirs[@]}" && isort --check-only "${dirs[@]}"
}

# Local pre-commit: rewrite the touched in-scope files in place (don't churn the whole tree).
_format_changed() {
  local -a changed
  mapfile -d '' -t changed < <(_changed_py)
  if [ ${#changed[@]} -gt 0 ]; then
    black "${changed[@]}" && isort "${changed[@]}"
  fi
}

if [ -n "${CI:-}" ]; then
  _check_format_scope
else
  _format_changed
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

#!/usr/bin/env bash
# scripts/ci/pr_readiness_labels.sh — make PR readiness visible on the PR index.
#
# WHY THIS EXISTS: the GitHub PR list shows the CI check rollup but never shows
# mergeability, so a PR with green checks and a merge conflict is visually
# IDENTICAL to one that is genuinely ready. On 2026-07-31 that hid a single
# deleted symlink (#164 → issue #168) that had made 6 of 7 open PRs unmergeable
# while every check stayed green and the nightly kept answering review findings
# that could never land. Labels are the only readiness signal the index renders,
# so this reconciles two of them:
#
#   ready-to-merge  nothing blocks a human merge: not a draft, no conflicts,
#                   checks green, and no unresolved review finding.
#   conflicts       merge-conflicted; further review rounds are wasted effort
#
# THE PREDICATE. `ready-to-merge` requires all three:
#   1. not a draft
#   2. mergeStateStatus == CLEAN — folds in "mergeable" AND "checks green" in
#      one field. UNSTABLE (mergeable, red non-required check) is deliberately
#      NOT ready; DIRTY is conflicted; BLOCKED is a required check outstanding.
#   3. zero LIVE review findings — a review thread that is unresolved.
#      `isResolved` is the authoritative signal; `isOutdated` is ignored.
#      The review-response skill resolves threads it addresses, so resolution
#      tracks real work rather than proxy heuristics (issue #169).
#
# THE ASYMMETRY. Granting is conservative; revocation is unconditional. Any
# PR that stops satisfying the predicate loses the chip on the next sweep, and
# the workflow sweeps on push-to-dev precisely because merging PR A is what
# conflicts PRs B..F — the label has to be revoked on OTHER PRs than the one
# that changed. A stale green chip is worse than no chip at all.
#
# UNKNOWN IS NEVER GUESSED, AND NEVER LEFT ADVERTISED. GitHub computes
# mergeability lazily and returns UNKNOWN until it lands, which is exactly the
# state right after a push to dev. Querying is itself what triggers the
# computation, so we re-query a few times rather than treat "not yet known" as
# "not conflicted". If it is still unknown, any held ready-to-merge is REVOKED —
# leaving it would reproduce the bug this script exists to prevent — and nothing
# else is asserted, `conflicts` included, since that is equally unverified.
#
# Idempotent by construction: it diffs desired against current labels and calls
# the API only on a real difference, so the hourly sweep does not churn every
# PR's timeline.
#
# Run: bash scripts/ci/pr_readiness_labels.sh [--dry-run] [--repo owner/name]
# Tests: bash scripts/ci/test_pr_readiness_labels.sh  (wired into scripts/gate.sh)
set -euo pipefail

REPO="${PR_LABELS_REPO:-fasrc/archi}"
READY_LABEL="${PR_LABELS_READY:-ready-to-merge}"
CONFLICT_LABEL="${PR_LABELS_CONFLICT:-conflicts}"
RETRY_MAX="${PR_LABELS_RETRY_MAX:-5}"
RETRY_DELAY="${PR_LABELS_RETRY_DELAY:-6}"
DRY_RUN=0

# Matches the `jobs.reconcile` key in .github/workflows/pr-readiness-labels.yml
# (which this script must NOT modify). Excluding it prevents the reconciler from
# blocking on its own in-progress check when triggered by pull_request events.
RECONCILER_JOB_NAME="${PR_LABELS_RECONCILER_JOB:-reconcile}"

# Connections are fetched one page deep; FILTER returns each totalCount so the
# reconciler can tell a complete snapshot from a truncated one. The two truncation
# cases are NOT symmetric:
#
#   reviewThreads truncated -> the live-findings count may be an UNDERCOUNT, so
#       readiness cannot be verified and the chip is withheld.
#   labels truncated -> the findings count is fine, but we cannot tell whether the
#       PR already HOLDS a managed chip. That matters more than it looks: if
#       `ready-to-merge` sat past the page we would read has_ready=no, schedule no
#       removal, and the PR would keep advertising readiness — the fail-closed
#       invariant, silently false. So we re-read the labels authoritatively
#       instead of guessing, and truncation alone does not withhold the chip.
#
# MUST equal the `first:N` in QUERY for both connections.
PAGE=100

usage() {
  cat <<'EOF'
Usage: pr_readiness_labels.sh [--dry-run] [--repo owner/name]

  --dry-run   decide and print, change nothing
  --repo      target repository (default: $PR_LABELS_REPO, else fasrc/archi)

Environment: PR_LABELS_REPO, PR_LABELS_READY, PR_LABELS_CONFLICT,
             PR_LABELS_RETRY_MAX, PR_LABELS_RETRY_DELAY,
             PR_LABELS_RECONCILER_JOB (the workflow job excluded from the
             blocking-check count; default: reconcile)
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --repo)
      shift
      if [ $# -eq 0 ]; then
        printf '%s: --repo needs an owner/name argument\n' "${0##*/}" >&2
        exit 2
      fi
      REPO="$1"
      ;;
    -h|--help) usage; exit 0 ;;
    *)
      printf '%s: unknown argument: %s\n' "${0##*/}" "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

case "$REPO" in
  */*) ;;
  *) printf '%s: repo must be owner/name, got: %s\n' "${0##*/}" "$REPO" >&2; exit 2 ;;
esac
OWNER="${REPO%%/*}"
NAME="${REPO##*/}"

# One query carries everything the predicate needs, so every PR in a sweep is
# judged against the same snapshot. `mergeStateStatus` needs no preview header.
QUERY='query($owner:String!,$name:String!,$cursor:String){
  repository(owner:$owner,name:$name){
    pullRequests(states:OPEN, first:50, after:$cursor){
      pageInfo{ hasNextPage endCursor }
      nodes{
        number isDraft mergeable mergeStateStatus
        labels(first:100){ totalCount nodes{ name } }
        reviewThreads(first:100){ totalCount nodes{ isResolved isOutdated } }
        commits(last:1){nodes{commit{statusCheckRollup{contexts(first:100){
          totalCount
          nodes{
            __typename
            ... on CheckRun{ name status conclusion }
            ... on StatusContext{ context state }
          }
        }}}}}
      }
    }
  }
}'

# Flatten to TSV: one PAGE row carrying the cursor, then one PR row each.
# `mergeable` and `mergeStateStatus` are carried for conflict detection and the
# UNKNOWN retry/revoke path; they are no longer consulted for the readiness
# clause, which uses individual check counts instead.
#
# Three additional columns carry the check-rollup summary:
#   blocking_checks  — count of non-excluded contexts that are not passing
#   rollup_total     — totalCount from the rollup connection (0 when null)
#   rollup_fetched   — count of contexts actually in the nodes array
# A null statusCheckRollup produces 0/0/0 ("no checks on record"). That is not
# read as "no block" unconditionally — the predicate cross-checks it against
# mergeStateStatus, because an empty rollup also describes a PR whose checks have
# not registered yet.
# CheckRun conclusions considered passing: SUCCESS, NEUTRAL, SKIPPED.
# StatusContext states considered passing: SUCCESS.
# A CheckRun whose name equals $excl is excluded from the blocking count.
FILTER='
  .data.repository.pullRequests as $p
  | (["PAGE", ($p.pageInfo.hasNextPage | tostring), ($p.pageInfo.endCursor // "")] | @tsv)
  , ( $p.nodes[]
      | (.commits.nodes[0].commit.statusCheckRollup // null) as $rollup
      | (if $rollup == null then 0 else $rollup.contexts.totalCount end) as $ct
      | (if $rollup == null then [] else ($rollup.contexts.nodes // []) end) as $cnodes
      | ($cnodes | length) as $cf
      | ($cnodes
         | map(select(.__typename != "CheckRun" or .name != $excl))
         | map(
             if .__typename == "CheckRun" then
               (.conclusion // "" | . != "SUCCESS" and . != "NEUTRAL" and . != "SKIPPED")
             else
               .state != "SUCCESS"
             end
           )
         | map(select(.))
         | length) as $blocking
      | [ "PR",
          (.number | tostring),
          (.isDraft | tostring),
          .mergeable,
          .mergeStateStatus,
          ([.reviewThreads.nodes[]
             | select(.isResolved == false)] | length | tostring),
          (.reviewThreads.totalCount | tostring),
          (.labels.totalCount | tostring),
          ([.labels.nodes[].name] | any(. == $ready) | tostring),
          ([.labels.nodes[].name] | any(. == $conflict) | tostring),
          ($blocking | tostring),
          ($ct | tostring),
          ($cf | tostring)
        ] | @tsv )
'

# mergeable and mergeStateStatus are BOTH carried, because they answer different
# questions and disagree in cases that matter. mergeStateStatus is a PRIORITY
# field: on a draft it reports DRAFT, masking DIRTY, so a conflicted draft looks
# unconflicted through that field alone. `mergeable` reports conflicts and nothing
# else. So `conflicts` is derived from mergeable == CONFLICTING, while readiness
# needs mergeStateStatus == CLEAN (which folds in draft, conflict AND check state).
#
# Label membership is computed HERE as exact string equality, not matched later
# against a flattened name list. GitHub permits a comma in a label name, so a
# glob over a comma-joined string would report a label named
# "blocked,ready-to-merge" as the managed chip — and a genuinely ready PR would
# then never receive the real one. Same for a configured name containing glob
# metacharacters.

# Every open PR as TSV rows.
#
# Every failure is checked EXPLICITLY rather than left to `set -e`, because
# `set -e` does not fire on a failed command substitution inside an assignment
# and the failure is not even visible to the caller: with `f(){ x="$(false)"; }`,
# `y="$(f)"` yields status 0. Relying on `set -e` here made an unreachable
# GitHub API look like a repository with zero open PRs — which would then strip
# every chip from every PR. An unreadable snapshot must be a loud operational
# failure, never an empty sweep.
fetch_snapshot() {
  local cursor="" page rows hasnext
  local -a gql
  while :; do
    gql=(api graphql -f "query=$QUERY" -F "owner=$OWNER" -F "name=$NAME")
    if [ -n "$cursor" ]; then
      gql+=(-F "cursor=$cursor")
    fi
    if ! page="$(gh "${gql[@]}")"; then
      printf '%s: GraphQL query failed for %s%s\n' \
        "${0##*/}" "$REPO" "${cursor:+ (page after $cursor)}" >&2
      return 1
    fi
    if ! rows="$(printf '%s' "$page" \
        | jq -r --arg ready "$READY_LABEL" --arg conflict "$CONFLICT_LABEL" \
               --arg excl "$RECONCILER_JOB_NAME" "$FILTER")"; then
      printf '%s: could not parse the GraphQL response for %s\n' "${0##*/}" "$REPO" >&2
      return 1
    fi
    printf '%s\n' "$rows" | awk -F'\t' '$1=="PR"'
    hasnext="$(printf '%s\n' "$rows" | awk -F'\t' '$1=="PAGE"{print $2; exit}')"
    cursor="$(printf '%s\n' "$rows" | awk -F'\t' '$1=="PAGE"{print $3; exit}')"
    if [ "$hasnext" != "true" ] || [ -z "$cursor" ]; then
      break
    fi
  done
}

# Exact membership of the two managed chips for one PR, as "<has_ready>
# <has_conflict>", from the paginated REST endpoint. Only called when the GraphQL
# labels connection was truncated, so the extra request costs nothing on the
# normal path. --paginate concatenates one JSON array per page, hence the
# slurp-and-add; `// []` covers the no-output case.
authoritative_membership() { # $1 = PR number
  local out
  if ! out="$(gh api "repos/$REPO/issues/$1/labels" --paginate)"; then
    return 1
  fi
  printf '%s' "$out" | jq -rs --arg ready "$READY_LABEL" --arg conflict "$CONFLICT_LABEL" \
    '(add // []) | map(.name) | "\(any(. == $ready)) \(any(. == $conflict))"'
}

# Re-query while any PR's mergeability is still being computed — the query is
# what prompts GitHub to compute it. Bounded, then we skip whatever is left.
snapshot=""
attempt=1
while :; do
  if ! snapshot="$(fetch_snapshot)"; then
    exit 1
  fi
  # UNKNOWN can surface in EITHER field (col 4 mergeable, col 5 mergeStateStatus);
  # both are computed asynchronously and either being unknown makes the PR
  # unverifiable.
  unknown="$(printf '%s\n' "$snapshot" \
    | awk -F'\t' '$4=="UNKNOWN" || $5=="UNKNOWN"' | grep -c . || true)"
  if [ "$unknown" -eq 0 ] || [ "$attempt" -ge "$RETRY_MAX" ]; then
    break
  fi
  printf 'mergeability still computing for %s PR(s) — retry %s/%s in %ss\n' \
    "$unknown" "$attempt" "$RETRY_MAX" "$RETRY_DELAY" >&2
  if [ "$RETRY_DELAY" != "0" ]; then
    sleep "$RETRY_DELAY"
  fi
  attempt=$((attempt + 1))
done

changed=0
unchanged=0
skipped=0
ready_now=0
failed=0
# Counted separately from changed/unchanged/skipped so those three stay DISJOINT
# and sum to the number of PRs seen. An UNKNOWN PR whose chip we revoke is
# `changed`, not `skipped` — but it is still unverifiable, which is what the
# warning at the end keys on.
unverifiable=0

while IFS=$'\t' read -r _tag number isdraft mergeable state live \
                        threads_total labels_total has_ready has_conflict \
                        blocking_checks rollup_total rollup_fetched; do
  if [ -z "${number:-}" ]; then
    continue
  fi

  # Truncated label connection: re-read authoritatively rather than conclude a chip
  # is absent because it fell off the page. Guessing here would break fail-closed.
  if [ "$labels_total" -gt "$PAGE" ]; then
    if ! membership="$(authoritative_membership "$number")"; then
      printf '%s: could not read the full label list for #%s (%s labels)\n' \
        "${0##*/}" "$number" "$labels_total" >&2
      failed=$((failed + 1))
      continue
    fi
    read -r has_ready has_conflict <<<"$membership"
  fi

  # Mergeability not computed even after the retries. We cannot verify readiness,
  # so we must stop ASSERTING it: revoke a held ready-to-merge and let a later
  # sweep re-grant it. Leaving the chip would reproduce the exact failure this
  # script exists to prevent — a push to dev conflicts a PR, the sweep cannot
  # read mergeability yet, and a green chip survives on a conflicted PR.
  # `conflicts` is left untouched: we genuinely do not know either way, and
  # asserting a conflict we cannot see would be the same sin in the other
  # direction.
  if [ "$state" = "UNKNOWN" ] || [ "$mergeable" = "UNKNOWN" ]; then
    unverifiable=$((unverifiable + 1))
    if [ "$has_ready" = false ]; then
      printf '#%-5s %-9s live=%-3s : skip (mergeability not computed)\n' \
        "$number" "UNKNOWN" "$live"
      skipped=$((skipped + 1))
      continue
    fi
    printf '#%-5s %-9s live=%-3s : --remove-label %s (unverifiable — mergeability not computed)\n' \
      "$number" "UNKNOWN" "$live" "$READY_LABEL"
    if [ "$DRY_RUN" -eq 0 ]; then
      if ! gh pr edit "$number" --repo "$REPO" --remove-label "$READY_LABEL" >/dev/null; then
        printf '%s: failed to revoke %s on #%s\n' "${0##*/}" "$READY_LABEL" "$number" >&2
        failed=$((failed + 1))
        continue
      fi
    fi
    changed=$((changed + 1))
    continue
  fi

  # From `mergeable`, NOT from mergeStateStatus == DIRTY. mergeStateStatus is a
  # priority field and reports DRAFT on a draft PR, masking DIRTY — so a
  # conflicted draft would get no chip, which is where it is arguably most
  # useful: it says why the PR cannot land even once it is marked ready.
  want_conflict=false
  if [ "$mergeable" = "CONFLICTING" ]; then
    want_conflict=true
  fi

  # The predicate. Each clause is a separate `if` rather than a `&&` chain
  # because under `set -e` a false `[ a ] && [ b ] && cmd` chain exits the
  # script instead of just skipping the command.
  #
  # Check state is now evaluated from individual rollup contexts, not from
  # mergeStateStatus. mergeStateStatus is still used for conflict detection and
  # the UNKNOWN retry path, but the readiness clause consults the blocking count.
  # A null rollup (no checks on record) is treated as 0 blocking — it does not
  # withhold. A truncated rollup (totalCount > fetched) is fail-closed.
  #
  # BEHIND is kept as an explicit clause because the rollup is BASE-AGNOSTIC. It
  # hangs off the PR's head commit and records that the checks passed, never which
  # base they were merged against. Retargeting a PR arrives as `edited` — which
  # this reconciler observes but neither check producer does (ci.yml uses the
  # default pull_request activity types, pr-preview.yml selects only
  # opened/synchronize/reopened) — so the head commit keeps the green rollup it
  # earned against the OLD base and blocking_checks reads 0. BEHIND is the one
  # base-relative signal GitHub hands us: the head ref is out of date, so the
  # checks on record cannot have tested the merge result. Withhold on it.
  #
  # This does NOT make the predicate fully base-aware — BEHIND is only reported
  # when the base requires branches to be up to date before merging. Where that
  # setting is off, a retargeted PR still reads CLEAN with stale green checks, and
  # no signal in the API distinguishes it. That residue is a branch-protection
  # setting, not something this script can close (issue #231).
  #
  # An EMPTY rollup is trusted only when the merge state agrees nothing is pending.
  # "No contexts on record" has two meanings the rollup alone cannot separate: a PR
  # that genuinely runs no checks, and a PR in the registration-lag window between
  # `opened`/`synchronize` and its first check run appearing. This reconciler fires
  # on those very events, so without a guard it grants the chip before CI has
  # produced a result. BLOCKED is what separates them: the base expects a required
  # check GitHub has not seen. Only BLOCKED — not UNSTABLE, which is derived from
  # non-passing contexts and so cannot describe a rollup that has none.
  #
  # This is deliberately NOT a general gate on BLOCKED — that would re-block the
  # reconciler on its own in-progress required check, which is issue #174, the
  # regression this change exists to remove. The clause is safe against that by
  # construction: when the reconciler's own job is running, its CheckRun sits on the
  # head commit, so rollup_total is at least 1 and this clause cannot fire. The
  # remaining BLOCKED slice — green checks plus a missing required approval — is a
  # non-empty rollup, still falls through, and is tracked in #231.
  want_ready=false
  why="blocking check"
  if [ "$isdraft" = "true" ]; then
    why="draft"
  elif [ "$mergeable" = "CONFLICTING" ]; then
    why="conflicting"
  elif [ "$state" = "BEHIND" ]; then
    why="behind the base — checks on record did not test the current base"
  elif [ "$rollup_total" -eq 0 ] && [ "$state" = "BLOCKED" ]; then
    why="no checks on record while GitHub reports BLOCKED — cannot verify"
  elif [ "$rollup_total" -gt "$rollup_fetched" ]; then
    why="rollup truncated ($rollup_total checks seen, $rollup_fetched fetched) — cannot verify"
  elif [ "$blocking_checks" -gt 0 ]; then
    why="$blocking_checks blocking check(s)"
  elif [ "$live" -gt 0 ]; then
    why="$live live review finding(s)"
  elif [ "$threads_total" -gt "$PAGE" ]; then
    # A live finding could be sitting in the unfetched tail, in which case `live`
    # undercounted. Withhold rather than advertise a readiness we did not verify.
    why="$threads_total review threads exceed the $PAGE fetched — cannot verify"
  else
    want_ready=true
    why=""
  fi

  edits=()
  if [ "$want_ready" = true ] && [ "$has_ready" = false ]; then
    edits+=(--add-label "$READY_LABEL")
  fi
  if [ "$want_ready" = false ] && [ "$has_ready" = true ]; then
    edits+=(--remove-label "$READY_LABEL")
  fi
  if [ "$want_conflict" = true ] && [ "$has_conflict" = false ]; then
    edits+=(--add-label "$CONFLICT_LABEL")
  fi
  if [ "$want_conflict" = false ] && [ "$has_conflict" = true ]; then
    edits+=(--remove-label "$CONFLICT_LABEL")
  fi

  if [ "$want_ready" = true ]; then
    ready_now=$((ready_now + 1))
  fi

  if [ ${#edits[@]} -eq 0 ]; then
    printf '#%-5s %-9s live=%-3s : unchanged%s\n' \
      "$number" "$state" "$live" "${why:+ ($why)}"
    unchanged=$((unchanged + 1))
    continue
  fi

  printf '#%-5s %-9s live=%-3s : %s%s\n' \
    "$number" "$state" "$live" "${edits[*]}" "${why:+ ($why)}"
  # One call per PR, both label changes batched: fewer API calls and a single
  # timeline entry instead of two.
  #
  # A failed write is recorded and the sweep CONTINUES. Aborting on the first
  # failure would leave every later PR unreconciled — including chips that need
  # revoking — so one PR with, say, a deleted label must not decide the fate of
  # the rest. The run still exits non-zero so CI goes red.
  if [ "$DRY_RUN" -eq 0 ]; then
    if ! gh pr edit "$number" --repo "$REPO" "${edits[@]}" >/dev/null; then
      printf '%s: failed to update labels on #%s\n' "${0##*/}" "$number" >&2
      failed=$((failed + 1))
      continue
    fi
  fi
  changed=$((changed + 1))
done <<< "$snapshot"

printf '\n%s: %s changed, %s unchanged, %s skipped%s%s — %s ready to merge%s\n' \
  "$REPO" "$changed" "$unchanged" "$skipped" \
  "$([ "$unverifiable" -gt 0 ] && printf ', %s unverifiable' "$unverifiable" || true)" \
  "$([ "$failed" -gt 0 ] && printf ', %s FAILED' "$failed" || true)" \
  "$ready_now" \
  "$([ "$DRY_RUN" -eq 1 ] && printf ' (dry run — nothing written)' || true)"

# Surface an incomplete reconciliation in the Actions UI WITHOUT failing the run.
# Exiting non-zero here would paint CI red for a routine GitHub lag — mergeability
# is computed asynchronously — which trains people to ignore the signal. The
# scheduled sweep is the retry, and the fail-safe revocation above already
# guarantees a skipped PR is never left ADVERTISING unverified readiness.
if [ "$unverifiable" -gt 0 ] && [ -n "${GITHUB_ACTIONS:-}" ]; then
  printf '::warning::%s PR(s) had unverifiable mergeability after %s attempt(s); any held %s was revoked and the next sweep retries\n' \
    "$unverifiable" "$RETRY_MAX" "$READY_LABEL"
fi

if [ "$failed" -gt 0 ]; then
  printf '%s: %s PR(s) could not be updated\n' "${0##*/}" "$failed" >&2
  exit 1
fi

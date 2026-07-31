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
#   ready-to-merge  a human can merge this now — the process is complete
#   conflicts       merge-conflicted; further review rounds are wasted effort
#
# THE PREDICATE. `ready-to-merge` requires all three:
#   1. not a draft
#   2. mergeStateStatus == CLEAN — folds in "mergeable" AND "checks green" in
#      one field. UNSTABLE (mergeable, red non-required check) is deliberately
#      NOT ready; DIRTY is conflicted; BLOCKED is a required check outstanding.
#   3. zero LIVE review findings — a review thread that is unresolved AND not
#      outdated.
#
# WHY `isOutdated` AND NOT `isResolved`. Codex posts findings as inline review
# threads (not issue comments), and in this repo no thread has EVER been marked
# resolved — so a predicate keyed on `isResolved` would label nothing, forever.
# GitHub sets `isOutdated` once a later push moved the code a finding pointed
# at, which is the available proxy for "addressed". It IS a proxy: a push that
# touches the same lines without fixing the finding also outdates it. The
# honest fix is for the review-response loop to resolve threads it has
# addressed, at which point `isResolved` becomes exact and belongs in the
# predicate too. Until then this errs toward the proxy rather than toward a
# chip that never appears.
#
# THE ASYMMETRY. Granting is conservative; revocation is unconditional. Any
# PR that stops satisfying the predicate loses the chip on the next sweep, and
# the workflow sweeps on push-to-dev precisely because merging PR A is what
# conflicts PRs B..F — the label has to be revoked on OTHER PRs than the one
# that changed. A stale green chip is worse than no chip at all.
#
# UNKNOWN IS SKIPPED, NEVER GUESSED. GitHub computes mergeability lazily and
# returns UNKNOWN until it lands, which is exactly the state right after a push
# to dev. Querying is itself what triggers the computation, so we re-query a
# few times rather than treat "not yet known" as "not conflicted".
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

# INCOMPLETE SNAPSHOT => NOT READY. Neither reviewThreads nor labels is
# paginated; instead FILTER compares each connection's totalCount against the page
# size and reports truncation, and a truncated PR is never granted the ready chip.
# Without this, a PR whose first 100 threads are all resolved/outdated but whose
# 101st is live would count zero live findings and be advertised as ready. The
# `> 100` tests in FILTER MUST equal the `first:100` in QUERY for both connections.

usage() {
  cat <<'EOF'
Usage: pr_readiness_labels.sh [--dry-run] [--repo owner/name]

  --dry-run   decide and print, change nothing
  --repo      target repository (default: $PR_LABELS_REPO, else fasrc/archi)

Environment: PR_LABELS_REPO, PR_LABELS_READY, PR_LABELS_CONFLICT,
             PR_LABELS_RETRY_MAX, PR_LABELS_RETRY_DELAY
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
      }
    }
  }
}'

# Flatten to TSV: one PAGE row carrying the cursor, then one PR row each.
# `mergeable` and `mergeStateStatus` are collapsed to a single UNKNOWN so the
# caller has one "not computed yet" state to test rather than two.
FILTER='
  .data.repository.pullRequests as $p
  | (["PAGE", ($p.pageInfo.hasNextPage | tostring), ($p.pageInfo.endCursor // "")] | @tsv)
  , ( $p.nodes[]
      | [ "PR",
          (.number | tostring),
          (.isDraft | tostring),
          (if .mergeable == "UNKNOWN" or .mergeStateStatus == "UNKNOWN"
             then "UNKNOWN" else .mergeStateStatus end),
          ([.reviewThreads.nodes[]
             | select(.isResolved == false and .isOutdated == false)] | length | tostring),
          (if .reviewThreads.totalCount > 100 then "threads=\(.reviewThreads.totalCount)>100"
           elif .labels.totalCount > 100 then "labels=\(.labels.totalCount)>100"
           else "no" end),
          ([.labels.nodes[].name] | join(","))
        ] | @tsv )
'

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
    if ! rows="$(printf '%s' "$page" | jq -r "$FILTER")"; then
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

# Re-query while any PR's mergeability is still being computed — the query is
# what prompts GitHub to compute it. Bounded, then we skip whatever is left.
snapshot=""
attempt=1
while :; do
  if ! snapshot="$(fetch_snapshot)"; then
    exit 1
  fi
  unknown="$(printf '%s\n' "$snapshot" | awk -F'\t' '$4=="UNKNOWN"' | grep -c . || true)"
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

while IFS=$'\t' read -r _tag number isdraft state live truncated labels; do
  if [ -z "${number:-}" ]; then
    continue
  fi

  has_ready=no
  has_conflict=no
  case ",$labels," in *",$READY_LABEL,"*) has_ready=yes ;; esac
  case ",$labels," in *",$CONFLICT_LABEL,"*) has_conflict=yes ;; esac

  # Mergeability not computed even after the retries. We cannot verify readiness,
  # so we must stop ASSERTING it: revoke a held ready-to-merge and let a later
  # sweep re-grant it. Leaving the chip would reproduce the exact failure this
  # script exists to prevent — a push to dev conflicts a PR, the sweep cannot
  # read mergeability yet, and a green chip survives on a conflicted PR.
  # `conflicts` is left untouched: we genuinely do not know either way, and
  # asserting a conflict we cannot see would be the same sin in the other
  # direction.
  if [ "$state" = "UNKNOWN" ]; then
    skipped=$((skipped + 1))
    if [ "$has_ready" = no ]; then
      printf '#%-5s %-9s live=%-3s : skip (mergeability not computed)\n' \
        "$number" "$state" "$live"
      continue
    fi
    printf '#%-5s %-9s live=%-3s : --remove-label %s (unverifiable — mergeability not computed)\n' \
      "$number" "$state" "$live" "$READY_LABEL"
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

  want_conflict=no
  if [ "$state" = "DIRTY" ]; then
    want_conflict=yes
  fi

  # The predicate. Each clause is a separate `if` rather than a `&&` chain
  # because under `set -e` a false `[ a ] && [ b ] && cmd` chain exits the
  # script instead of just skipping the command.
  want_ready=no
  why="not CLEAN ($state)"
  if [ "$isdraft" = "true" ]; then
    why="draft"
  elif [ "$state" != "CLEAN" ]; then
    why="not CLEAN ($state)"
  elif [ "$live" -gt 0 ]; then
    why="$live live review finding(s)"
  elif [ "$truncated" != "no" ]; then
    # A live finding could be sitting in the unfetched tail, in which case `live`
    # undercounted. Withhold rather than advertise a readiness we did not verify.
    why="incomplete snapshot ($truncated) — cannot verify"
  else
    want_ready=yes
    why=""
  fi

  edits=()
  if [ "$want_ready" = yes ] && [ "$has_ready" = no ]; then
    edits+=(--add-label "$READY_LABEL")
  fi
  if [ "$want_ready" = no ] && [ "$has_ready" = yes ]; then
    edits+=(--remove-label "$READY_LABEL")
  fi
  if [ "$want_conflict" = yes ] && [ "$has_conflict" = no ]; then
    edits+=(--add-label "$CONFLICT_LABEL")
  fi
  if [ "$want_conflict" = no ] && [ "$has_conflict" = yes ]; then
    edits+=(--remove-label "$CONFLICT_LABEL")
  fi

  if [ "$want_ready" = yes ]; then
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

printf '\n%s: %s changed, %s unchanged, %s skipped — %s ready to merge%s\n' \
  "$REPO" "$changed" "$unchanged" "$skipped" "$ready_now" \
  "$([ "$DRY_RUN" -eq 1 ] && printf ' (dry run — nothing written)' || true)"

# Surface an incomplete reconciliation in the Actions UI WITHOUT failing the run.
# Exiting non-zero here would paint CI red for a routine GitHub lag — mergeability
# is computed asynchronously — which trains people to ignore the signal. The
# scheduled sweep is the retry, and the fail-safe revocation above already
# guarantees a skipped PR is never left ADVERTISING unverified readiness.
if [ "$skipped" -gt 0 ] && [ -n "${GITHUB_ACTIONS:-}" ]; then
  printf '::warning::%s PR(s) skipped — mergeability not computed after %s attempt(s); the next sweep retries\n' \
    "$skipped" "$RETRY_MAX"
fi

if [ "$failed" -gt 0 ]; then
  printf '%s: %s PR(s) could not be updated\n' "${0##*/}" "$failed" >&2
  exit 1
fi

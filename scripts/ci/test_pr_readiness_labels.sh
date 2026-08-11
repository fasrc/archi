#!/usr/bin/env bash
# Self-test for pr_readiness_labels.sh: exercises the reconciler's contract
# against a fake `gh` — no network, no GitHub, nothing mutated anywhere.
#
# The contract under test is a PREDICATE plus an ASYMMETRY. `ready-to-merge`
# means "a human can merge this now": not a draft, mergeStateStatus CLEAN
# (mergeable AND checks green), and zero LIVE review findings. A finding is
# live when it is unresolved — `isResolved` is the authoritative signal,
# regardless of `isOutdated`. `conflicts` means mergeStateStatus DIRTY and
# nothing else.
#
#    1-2. the happy path adds the chip, and a live finding withholds it
#      3. an unresolved-but-outdated finding STILL withholds it
#      4. a resolved finding does NOT withhold it
#     5.  DIRTY earns the conflicts chip
#     6.  DIRTY REVOKES a stale ready-to-merge — the asymmetry that makes a
#         green chip trustworthy is that revocation is unconditional
#     7.  a draft is never ready, however clean
#     8.  clearing a conflict removes the conflicts chip
#     9.  idempotence: correct labels already present => ZERO write calls, so
#         the hourly sweep does not spam every PR's timeline with label churn
#    10.  UNKNOWN mergeability grants nothing — GitHub computes it
#         asynchronously and returns UNKNOWN until it lands
#    11.  ...but a retry picks it up once GitHub has computed it, because a
#         push to dev makes every PR UNKNOWN at exactly the moment the sweep
#         matters most
#    12.  UNSTABLE (mergeable, red check) earns NEITHER chip
#    13.  --dry-run decides and prints but never writes
#    14.  pagination is followed, so PR 51+ is not silently unlabelled
#    15.  a gh failure is an operational failure, not a silent no-op — an
#         unreachable API must never read as "zero open PRs", which would
#         strip every chip in the repository
#    16.  ...but a single failed write does not abort the remaining PRs
#    17.  UNKNOWN REVOKES a chip it cannot verify — a merge to dev conflicts a
#         PR before GitHub recomputes, and a green chip must not survive that
#    18.  a TRUNCATED reviewThreads connection is never ready — an unfetched live
#         finding would otherwise count as zero
#   19-20. a TRUNCATED labels connection is RE-READ authoritatively, so a chip
#         hiding past the page is still revoked rather than read as absent; and a
#         failed re-read is a failure, never a fallback to the partial set
#
# Every negative case seeds a PR that already HOLDS the chip and requires its
# removal. Asserting only "no add-label happened" would pass for a reconciler
# that ignored the PR entirely — a test that cannot fail is not a test.
# Run: bash scripts/ci/test_pr_readiness_labels.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RECONCILER="$SCRIPT_DIR/pr_readiness_labels.sh"

# Hermetic against the developer's own environment: every knob the reconciler
# honours is cleared once, so an ambient PR_LABELS_REPO cannot point this suite
# at a real repository and an ambient delay cannot make it sleep.
unset PR_LABELS_REPO PR_LABELS_RETRY_MAX PR_LABELS_RETRY_DELAY \
  PR_LABELS_READY PR_LABELS_CONFLICT

PASS=0; FAIL=0
ok()    { printf 'ok - %s\n' "$1"; PASS=$((PASS + 1)); }
notok() { printf 'not ok - %s\n' "$1"; FAIL=$((FAIL + 1)); }

# Fail by NAME when the reconciler is missing. Without this, the first case's
# command substitution dies under `set -e` and the suite exits 127 having
# printed nothing at all — indistinguishable, to a careless reader, from a
# clean pass.
[ -f "$RECONCILER" ] || {
  printf 'not ok - reconciler not found at %s\n' "$RECONCILER"
  printf '\n0 passed, 1 failed\n'
  exit 1
}

TESTROOT="$(mktemp -d)"
trap 'rm -rf "$TESTROOT"' EXIT

# ---- fixture builders -------------------------------------------------------
# threads spec: comma-separated "isResolved:isOutdated", e.g. "false:false,true:false"
mk_threads() {
  local spec="${1:-}" out="" t r o
  [ -z "$spec" ] && { printf '[]'; return; }
  local IFS=','
  for t in $spec; do
    r="${t%%:*}"; o="${t##*:}"
    out+="{\"isResolved\":$r,\"isOutdated\":$o},"
  done
  printf '[%s]' "${out%,}"
}

# A comma-separated list of label names, OR raw JSON when it starts with `[` —
# needed because a GitHub label name may itself contain a comma, which the CSV
# form cannot express and which is exactly the ambiguity case 22 exercises.
mk_labels() {
  local spec="${1:-}" out="" l
  case "$spec" in
    "")   printf '[]'; return ;;
    \[*)  printf '%s' "$spec"; return ;;
  esac
  local IFS=','
  for l in $spec; do out+="{\"name\":\"$l\"},"; done
  printf '[%s]' "${out%,}"
}

# mk_checks [spec,spec,...]
# Each spec is one of:
#   C:<name>:<status>:<conclusion>   CheckRun  (conclusion: null or SUCCESS/FAILURE/etc.)
#   S:<context>:<state>              StatusContext
# Returns a JSON array suitable for the checks-json parameter of mk_node().
mk_checks() {
  local spec="${1:-}" out="" entry rest name status conclusion context state
  [ -z "$spec" ] && { printf '[]'; return; }
  local IFS=','
  for entry in $spec; do
    rest="${entry#*:}"
    case "$entry" in
      C:*)
        name="${rest%%:*}"; rest="${rest#*:}"
        status="${rest%%:*}"; conclusion="${rest##*:}"
        if [ "$conclusion" = "null" ]; then
          out+="{\"__typename\":\"CheckRun\",\"name\":\"$name\",\"status\":\"$status\",\"conclusion\":null},"
        else
          out+="{\"__typename\":\"CheckRun\",\"name\":\"$name\",\"status\":\"$status\",\"conclusion\":\"$conclusion\"},"
        fi
        ;;
      S:*)
        context="${rest%%:*}"; state="${rest##*:}"
        out+="{\"__typename\":\"StatusContext\",\"context\":\"$context\",\"state\":\"$state\"},"
        ;;
    esac
  done
  printf '[%s]' "${out%,}"
}

# mk_node <number> <isDraft> <mergeStateStatus> <labels> <threads-csv> \
#         [threads-totalCount] [labels-totalCount] [mergeable] \
#         [checks-json] [checks-totalCount]
#
# `mergeable` defaults to a value consistent with the merge state, but is
# OVERRIDABLE, because the two fields are independent in the real API and the
# interesting combinations are the ones where they disagree: a conflicted DRAFT
# reports mergeable=CONFLICTING while mergeStateStatus=DRAFT masks it. An earlier
# version derived mergeable solely from the state, which made that real
# combination unrepresentable — the fixture was hiding a live bug.
#
# Each connection's totalCount is counted from the nodes actually supplied; the
# overrides exist to simulate a TRUNCATED connection where totalCount exceeds
# what was fetched.
#
# checks-json: a JSON array of context objects built by mk_checks(). Omit or
#              pass "" for the default null rollup (PR has no checks on record).
#              The FILTER treats a null statusCheckRollup as 0/0/0.
# checks-totalCount: override to simulate a truncated rollup where totalCount
#              exceeds the fetched contexts; defaults to the array's actual length.
mk_node() {
  local n="$1" draft="$2" state="$3" labels="${4:-}" threads="${5:-}"
  local tt="${6:-}" lt="${7:-}" mergeable="${8:-}"
  local checks="${9:-}" ct="${10:-}"
  local tcount lcount ljson tjson cjson ccount rollup_json
  if [ -z "$mergeable" ]; then
    case "$state" in
      UNKNOWN) mergeable=UNKNOWN ;;
      DIRTY)   mergeable=CONFLICTING ;;
      *)       mergeable=MERGEABLE ;;
    esac
  fi
  ljson="$(mk_labels "$labels")"
  tjson="$(mk_threads "$threads")"
  # Count from the built JSON, so a label name containing a comma cannot skew it.
  lcount="$(printf '%s' "$ljson" | jq 'length')"
  tcount="$(printf '%s' "$tjson" | jq 'length')"
  if [ -n "$tt" ]; then tcount="$tt"; fi
  if [ -n "$lt" ]; then lcount="$lt"; fi
  # Build the statusCheckRollup. Null when no checks are requested (default),
  # so all pre-existing cases keep working unchanged. Non-null when a checks
  # array or a totalCount override is given, so truncation can be simulated.
  if [ -n "$checks" ] || [ -n "$ct" ]; then
    cjson="${checks:-[]}"
    ccount="$(printf '%s' "$cjson" | jq 'length')"
    if [ -n "$ct" ]; then ccount="$ct"; fi
    rollup_json="{\"contexts\":{\"totalCount\":$ccount,\"nodes\":$cjson}}"
  else
    rollup_json='null'
  fi
  printf '{"number":%s,"isDraft":%s,"mergeable":"%s","mergeStateStatus":"%s","labels":{"totalCount":%s,"nodes":%s},"reviewThreads":{"totalCount":%s,"nodes":%s},"commits":{"nodes":[{"commit":{"statusCheckRollup":%s}}]}}' \
    "$n" "$draft" "$mergeable" "$state" "$lcount" "$ljson" "$tcount" "$tjson" "$rollup_json"
}

# mk_page <hasNextPage> <endCursor> <node-json>...
mk_page() {
  local hn="$1" cur="$2"; shift 2
  local nodes="" n
  for n in "$@"; do nodes+="$n,"; done
  printf '{"data":{"repository":{"pullRequests":{"pageInfo":{"hasNextPage":%s,"endCursor":"%s"},"nodes":[%s]}}}}' \
    "$hn" "$cur" "${nodes%,}"
}

# A fake `gh` that records every argv and serves canned GraphQL responses in
# sequence: resp_1.json on the first `api graphql`, resp_2.json on the second,
# falling back to the highest-numbered file that exists. One mechanism covers
# both pagination (page 2 is the next response) and the UNKNOWN retry (the
# recomputed snapshot is the next response), so a test just lays out the
# sequence it expects to happen.
make_stub() { # $1 = sandbox
  local sb="$1"
  mkdir -p "$sb/bin"
  cat > "$sb/bin/gh" <<EOF
#!/usr/bin/env bash
printf '%s\n' "\$*" >> "$sb/calls"
# The authoritative label re-read: repos/<owner>/<name>/issues/<N>/labels
case "\$2" in
  repos/*/issues/*/labels)
    if [ "\$1" = "api" ]; then
      [ -f "$sb/fail_labels" ] && { echo "stub gh: labels boom" >&2; exit 1; }
      pr=\$(basename "\$(dirname "\$2")")
      cat "$sb/labels_\$pr.json" 2>/dev/null || printf '[]'
      exit 0
    fi
    ;;
esac
if [ "\$1" = "api" ] && [ "\$2" = "graphql" ]; then
  n=\$(( \$(cat "$sb/n" 2>/dev/null || echo 0) + 1 ))
  printf '%s' "\$n" > "$sb/n"
  [ -f "$sb/fail_graphql" ] && { echo "stub gh: GraphQL boom" >&2; exit 1; }
  while [ "\$n" -gt 1 ] && [ ! -f "$sb/resp_\$n.json" ]; do n=\$(( n - 1 )); done
  cat "$sb/resp_\$n.json"
  exit 0
fi
[ -f "$sb/fail_write" ] && { echo "stub gh: write boom" >&2; exit 1; }
exit 0
EOF
  chmod +x "$sb/bin/gh"
}

# Fresh sandbox with the fake gh ahead of the real one. Retries are instant and
# capped at 1 attempt unless a test asks otherwise, so the suite never sleeps.
new_sandbox() {
  local sb; sb="$(mktemp -d "$TESTROOT/sb.XXXXXX")"
  make_stub "$sb"
  printf '%s' "$sb"
}

run_reconciler() { # $1 = sandbox; remaining args passed to the reconciler
  local sb="$1"; shift
  PATH="$sb/bin:$PATH" \
  PR_LABELS_REPO="${PR_LABELS_REPO-acme/widgets}" \
  PR_LABELS_RETRY_MAX="${PR_LABELS_RETRY_MAX-1}" \
  PR_LABELS_RETRY_DELAY="${PR_LABELS_RETRY_DELAY-0}" \
    bash "$RECONCILER" "$@"
}

# Write calls are the ones that mutate: `gh pr edit ... --add-label/--remove-label`.
write_calls() { grep -c -- '--add-label\|--remove-label' "$1/calls" 2>/dev/null || true; }

# ---- 1: clean, no findings, unlabelled -> gets the chip ---------------------
# Also pins the query arguments: a reconciler that swept the wrong repository
# would otherwise satisfy every other case in this file.
sb="$(new_sandbox)"
mk_page false "" "$(mk_node 153 false CLEAN "" "")" > "$sb/resp_1.json"
out="$(run_reconciler "$sb" 2>&1)"
if grep -q -- '--add-label ready-to-merge' "$sb/calls" \
   && grep -q '153' <<<"$out" \
   && grep -q 'owner=acme' "$sb/calls" \
   && grep -q 'name=widgets' "$sb/calls" \
   && grep -q -- '--repo acme/widgets' "$sb/calls"; then
  ok "clean PR with no live findings gets ready-to-merge, from the named repo"
else
  notok "clean PR with no live findings gets ready-to-merge, from the named repo"
  printf '%s\n' "$out"; cat "$sb/calls" 2>/dev/null
fi

# ---- 2: clean but a live finding -> withheld AND a stale chip revoked -------
# Every negative case below carries TWO PRs: one already holding the chip (which
# must be REMOVED) and one without it (which must not gain it). Asserting only
# "no add happened" would pass for a reconciler that skipped both PRs entirely —
# a vacuous test that cannot fail.
sb="$(new_sandbox)"
mk_page false "" \
  "$(mk_node 154 false CLEAN "ready-to-merge" "false:false,false:false")" \
  "$(mk_node 254 false CLEAN "" "false:false")" > "$sb/resp_1.json"
run_reconciler "$sb" >/dev/null 2>&1
if grep -q '154 .*--remove-label ready-to-merge' "$sb/calls" \
   && ! grep -q -- '--add-label ready-to-merge' "$sb/calls"; then
  ok "a live (unresolved, not outdated) finding withholds and revokes ready-to-merge"
else
  notok "a live (unresolved, not outdated) finding withholds and revokes ready-to-merge"
  cat "$sb/calls" 2>/dev/null
fi

# ---- 3: an unresolved-but-outdated finding STILL withholds ----------------
# isResolved is the signal; isOutdated is irrelevant. An unresolved thread
# blocks the chip whether or not a later push outdated it.
sb="$(new_sandbox)"
mk_page false "" \
  "$(mk_node 158 false CLEAN "ready-to-merge" "false:true")" \
  "$(mk_node 258 false CLEAN "" "false:true")" > "$sb/resp_1.json"
run_reconciler "$sb" >/dev/null 2>&1
if grep -q '158 .*--remove-label ready-to-merge' "$sb/calls" \
   && ! grep -q -- '--add-label ready-to-merge' "$sb/calls"; then
  ok "an unresolved-but-outdated finding withholds and revokes ready-to-merge"
else
  notok "an unresolved-but-outdated finding withholds and revokes ready-to-merge"
  cat "$sb/calls" 2>/dev/null
fi

# ---- 4: only a RESOLVED finding -> still ready -----------------------------
sb="$(new_sandbox)"
mk_page false "" "$(mk_node 158 false CLEAN "" "true:false")" > "$sb/resp_1.json"
run_reconciler "$sb" >/dev/null 2>&1
if grep -q -- '--add-label ready-to-merge' "$sb/calls"; then
  ok "a resolved finding does not withhold ready-to-merge"
else
  notok "a resolved finding does not withhold ready-to-merge"
fi

# ---- 5: DIRTY -> conflicts chip -------------------------------------------
sb="$(new_sandbox)"
mk_page false "" "$(mk_node 159 false DIRTY "" "false:false")" > "$sb/resp_1.json"
run_reconciler "$sb" >/dev/null 2>&1
if grep -q -- '--add-label conflicts' "$sb/calls"; then
  ok "a DIRTY PR gets the conflicts chip"
else
  notok "a DIRTY PR gets the conflicts chip"
fi

# ---- 6: DIRTY revokes a stale ready-to-merge ------------------------------
sb="$(new_sandbox)"
mk_page false "" "$(mk_node 160 false DIRTY "ready-to-merge" "")" > "$sb/resp_1.json"
run_reconciler "$sb" >/dev/null 2>&1
if grep -q -- '--remove-label ready-to-merge' "$sb/calls" \
   && grep -q -- '--add-label conflicts' "$sb/calls"; then
  ok "a newly-conflicted PR has its stale ready-to-merge revoked"
else
  notok "a newly-conflicted PR has its stale ready-to-merge revoked"
  cat "$sb/calls" 2>/dev/null
fi

# ---- 7: a draft is never ready -------------------------------------------
sb="$(new_sandbox)"
mk_page false "" \
  "$(mk_node 161 true CLEAN "ready-to-merge" "")" \
  "$(mk_node 261 true CLEAN "" "")" > "$sb/resp_1.json"
run_reconciler "$sb" >/dev/null 2>&1
if grep -q '161 .*--remove-label ready-to-merge' "$sb/calls" \
   && ! grep -q -- '--add-label ready-to-merge' "$sb/calls"; then
  ok "converting to a draft revokes ready-to-merge, and a draft never gains it"
else
  notok "converting to a draft revokes ready-to-merge, and a draft never gains it"
  cat "$sb/calls" 2>/dev/null
fi

# ---- 8: conflict cleared -> conflicts chip removed -----------------------
sb="$(new_sandbox)"
mk_page false "" "$(mk_node 145 false CLEAN "conflicts" "false:false")" > "$sb/resp_1.json"
run_reconciler "$sb" >/dev/null 2>&1
if grep -q -- '--remove-label conflicts' "$sb/calls"; then
  ok "clearing a conflict removes the conflicts chip"
else
  notok "clearing a conflict removes the conflicts chip"
fi

# ---- 9: idempotence — already correct means ZERO writes ------------------
sb="$(new_sandbox)"
mk_page false "" \
  "$(mk_node 153 false CLEAN "ready-to-merge" "")" \
  "$(mk_node 159 false DIRTY "conflicts" "false:false")" \
  "$(mk_node 154 false CLEAN "" "false:false")" > "$sb/resp_1.json"
run_reconciler "$sb" >/dev/null 2>&1
if [ "$(write_calls "$sb")" = "0" ]; then
  ok "an already-correct sweep makes zero write calls"
else
  notok "an already-correct sweep makes zero write calls"
  cat "$sb/calls" 2>/dev/null
fi

# ---- 10: UNKNOWN is skipped, never guessed ------------------------------
sb="$(new_sandbox)"
mk_page false "" "$(mk_node 170 false UNKNOWN "" "")" > "$sb/resp_1.json"
out="$(run_reconciler "$sb" 2>&1)"
if [ "$(write_calls "$sb")" = "0" ] && grep -qi 'skip' <<<"$out"; then
  ok "UNKNOWN mergeability is skipped, not guessed"
else
  notok "UNKNOWN mergeability is skipped, not guessed"; printf '%s\n' "$out"
fi

# ---- 11: ...but a retry picks it up once GitHub computes it --------------
sb="$(new_sandbox)"
mk_page false "" "$(mk_node 170 false UNKNOWN "" "")" > "$sb/resp_1.json"
mk_page false "" "$(mk_node 170 false CLEAN   "" "")" > "$sb/resp_2.json"
PR_LABELS_RETRY_MAX=2 run_reconciler "$sb" >/dev/null 2>&1
if grep -q -- '--add-label ready-to-merge' "$sb/calls"; then
  ok "a retry labels a PR whose mergeability was still being computed"
else
  notok "a retry labels a PR whose mergeability was still being computed"
  cat "$sb/calls" 2>/dev/null
fi

# ---- 12: UNSTABLE (mergeable, red check) earns neither chip -------------
# A check going red on a PR that already holds the chip must revoke it; and
# UNSTABLE must not attract `conflicts` either, since the PR is not conflicted.
# Provides an actual failing CheckRun so the individual-check predicate can see
# the blocking conclusion — required now that the predicate reads check contexts
# rather than consulting mergeStateStatus directly.
sb="$(new_sandbox)"
_fail_check="$(mk_checks "C:some-check:COMPLETED:FAILURE")"
mk_page false "" \
  "$(mk_node 162 false UNSTABLE "ready-to-merge" "" "" "" "" "$_fail_check")" \
  "$(mk_node 262 false UNSTABLE "" "" "" "" "" "$_fail_check")" > "$sb/resp_1.json"
run_reconciler "$sb" >/dev/null 2>&1
if grep -q '162 .*--remove-label ready-to-merge' "$sb/calls" \
   && ! grep -q -- '--add-label' "$sb/calls"; then
  ok "a red check revokes ready-to-merge and earns no conflicts chip"
else
  notok "a red check revokes ready-to-merge and earns no conflicts chip"
  cat "$sb/calls" 2>/dev/null
fi

# ---- 13: --dry-run decides but never writes ----------------------------
sb="$(new_sandbox)"
mk_page false "" "$(mk_node 153 false CLEAN "" "")" > "$sb/resp_1.json"
out="$(run_reconciler "$sb" --dry-run 2>&1)"
if [ "$(write_calls "$sb")" = "0" ] && grep -q 'ready-to-merge' <<<"$out"; then
  ok "--dry-run prints the decision and makes no write calls"
else
  notok "--dry-run prints the decision and makes no write calls"; printf '%s\n' "$out"
fi

# ---- 14: pagination is followed --------------------------------------
sb="$(new_sandbox)"
mk_page true  "CUR1" "$(mk_node 153 false CLEAN "" "")" > "$sb/resp_1.json"
mk_page false ""     "$(mk_node 251 false DIRTY "" "")" > "$sb/resp_2.json"
run_reconciler "$sb" >/dev/null 2>&1
# The cursor assertion matters: the stub serves pages by call order, so without
# it this case would pass even if the second request carried no cursor at all
# (which against the real API would re-fetch page 1 forever).
if grep -q -- '--add-label ready-to-merge' "$sb/calls" \
   && grep -q '251 .*--add-label conflicts' "$sb/calls" \
   && grep -q 'cursor=CUR1' "$sb/calls"; then
  ok "pagination is followed with the endCursor from the previous page"
else
  notok "pagination is followed with the endCursor from the previous page"
  cat "$sb/calls" 2>/dev/null
fi

# ---- 15: a gh failure is an operational failure ---------------------
sb="$(new_sandbox)"
mk_page false "" "$(mk_node 153 false CLEAN "" "")" > "$sb/resp_1.json"
: > "$sb/fail_graphql"
if run_reconciler "$sb" >/dev/null 2>&1; then
  notok "a failing gh query exits non-zero"
else
  ok "a failing gh query exits non-zero"
fi

# ---- 16: a write failure is recorded but does not abort the sweep ----
# Aborting on the first bad PR would leave every later one unreconciled,
# including chips that need REVOKING — the failure mode that matters most.
sb="$(new_sandbox)"
mk_page false "" \
  "$(mk_node 153 false CLEAN "" "")" \
  "$(mk_node 159 false DIRTY "" "")" > "$sb/resp_1.json"
: > "$sb/fail_write"
if run_reconciler "$sb" >/dev/null 2>&1; then
  notok "a failed label write exits non-zero but still sweeps the rest"
elif [ "$(grep -c -- '--add-label' "$sb/calls")" = "2" ]; then
  ok "a failed label write exits non-zero but still sweeps the rest"
else
  notok "a failed label write exits non-zero but still sweeps the rest (later PRs skipped)"
  cat "$sb/calls" 2>/dev/null
fi

# ---- 17: UNKNOWN revokes a chip it cannot verify -----------------------
# The dangerous case this exists for: a merge to dev conflicts a PR, the sweep
# runs before GitHub has recomputed mergeability, and a green chip would
# otherwise survive on a now-conflicted PR until some later sweep.
sb="$(new_sandbox)"
mk_page false "" \
  "$(mk_node 170 false UNKNOWN "ready-to-merge" "")" \
  "$(mk_node 270 false UNKNOWN "" "")" > "$sb/resp_1.json"
out="$(run_reconciler "$sb" 2>&1)"
# The summary assertion also pins the counter arithmetic: changed/unchanged/
# skipped must stay DISJOINT and sum to the 2 PRs seen (1 + 0 + 1), while
# `unverifiable` counts both UNKNOWN PRs and overlaps deliberately. An earlier
# version incremented `skipped` for a PR it had also counted as `changed`, so the
# tallies summed to more PRs than existed.
if grep -q '170 .*--remove-label ready-to-merge' "$sb/calls" \
   && ! grep -q -- '--add-label' "$sb/calls" \
   && ! grep -q '270 .*--remove-label' "$sb/calls" \
   && grep -q '1 changed, 0 unchanged, 1 skipped, 2 unverifiable' <<<"$out"; then
  ok "UNKNOWN revokes an unverifiable ready-to-merge, asserts nothing new, counts once"
else
  notok "UNKNOWN revokes an unverifiable ready-to-merge, asserts nothing new, counts once"
  printf '%s\n' "$out"; cat "$sb/calls" 2>/dev/null
fi

# ---- 18: a truncated reviewThreads connection is not ready ------------
# 143 threads exist but only 100 were fetched, and all 100 read as addressed —
# so `live` is 0 and the naive predicate would grant the chip while a live
# finding sits in the unfetched tail.
sb="$(new_sandbox)"
mk_page false "" \
  "$(mk_node 180 false CLEAN "ready-to-merge" "" 143)" \
  "$(mk_node 280 false CLEAN "" "" 143)" > "$sb/resp_1.json"
run_reconciler "$sb" >/dev/null 2>&1
if grep -q '180 .*--remove-label ready-to-merge' "$sb/calls" \
   && ! grep -q -- '--add-label ready-to-merge' "$sb/calls"; then
  ok "a truncated reviewThreads connection withholds and revokes ready-to-merge"
else
  notok "a truncated reviewThreads connection withholds and revokes ready-to-merge"
  cat "$sb/calls" 2>/dev/null
fi

# ---- 19: a chip HIDDEN past the label page is still revoked -----------
# The subtle one. 120 labels exist, so the fetched page is truncated, and
# `ready-to-merge` is NOT among the names returned — only in the authoritative
# list. Reading has_ready from the truncated page would conclude the chip is
# absent, schedule no removal, and leave the PR advertising readiness: the
# fail-closed invariant silently false. The PR also has a live finding, so it is
# definitively not ready.
sb="$(new_sandbox)"
mk_page false "" "$(mk_node 190 false CLEAN "some-other-label" "false:false" "" 120)" > "$sb/resp_1.json"
printf '[{"name":"some-other-label"},{"name":"ready-to-merge"}]' > "$sb/labels_190.json"
run_reconciler "$sb" >/dev/null 2>&1
if grep -q '190 .*--remove-label ready-to-merge' "$sb/calls"; then
  ok "a chip past the label page is found authoritatively and revoked"
else
  notok "a chip past the label page is found authoritatively and revoked"
  cat "$sb/calls" 2>/dev/null
fi

# ---- 20: a failed authoritative label read is a failure, not a guess --
# If we cannot establish the real label set we must not proceed on the truncated
# one; that is how the invariant above would break.
sb="$(new_sandbox)"
mk_page false "" \
  "$(mk_node 191 false CLEAN "x" "false:false" "" 120)" \
  "$(mk_node 291 false DIRTY "" "")" > "$sb/resp_1.json"
: > "$sb/fail_labels"
if run_reconciler "$sb" >/dev/null 2>&1; then
  notok "a failed authoritative label read exits non-zero and sweeps the rest"
elif grep -q '291 .*--add-label conflicts' "$sb/calls"; then
  ok "a failed authoritative label read exits non-zero and sweeps the rest"
else
  notok "a failed authoritative label read exits non-zero and sweeps the rest (later PRs skipped)"
  cat "$sb/calls" 2>/dev/null
fi

# ---- 21: a conflicted DRAFT still earns the conflicts chip -------------
# mergeStateStatus is a PRIORITY field: on a draft it reports DRAFT and masks
# DIRTY, while `mergeable` independently reports CONFLICTING. Deriving the
# conflicts chip from the merge STATE therefore hides a real conflict behind
# draft status — the chip is arguably most useful there, since it says why the
# PR cannot land even once it is marked ready.
sb="$(new_sandbox)"
mk_page false "" "$(mk_node 210 true DRAFT "" "" "" "" CONFLICTING)" > "$sb/resp_1.json"
run_reconciler "$sb" >/dev/null 2>&1
if grep -q '210 .*--add-label conflicts' "$sb/calls" \
   && ! grep -q -- '--add-label ready-to-merge' "$sb/calls"; then
  ok "a conflicted draft earns conflicts and never ready-to-merge"
else
  notok "a conflicted draft earns conflicts and never ready-to-merge"
  cat "$sb/calls" 2>/dev/null
fi

# ---- 22: label membership is exact, not a substring -------------------
# GitHub permits a comma in a label name. Flattening names to a comma-joined
# string and glob-matching ",ready-to-merge," reports this PR as already holding
# the chip, so a genuinely ready PR would never receive the real one.
sb="$(new_sandbox)"
mk_page false "" "$(mk_node 220 false CLEAN '[{"name":"blocked,ready-to-merge"}]' "")" > "$sb/resp_1.json"
run_reconciler "$sb" >/dev/null 2>&1
if grep -q '220 .*--add-label ready-to-merge' "$sb/calls"; then
  ok "a label whose name merely contains the chip name does not count as holding it"
else
  notok "a label whose name merely contains the chip name does not count as holding it"
  cat "$sb/calls" 2>/dev/null
fi

# ---- 23: reconcile in-progress is excluded; PR with no other blocking check → ready ---
# A non-draft MERGEABLE PR whose only non-successful check is the reconciler's own
# job (name: "reconcile", IN_PROGRESS, null conclusion) should get ready-to-merge.
# The old predicate sees UNSTABLE (not CLEAN) and withholds it — this case is the
# red step that drove the new individual-check predicate.
sb="$(new_sandbox)"
mk_page false "" \
  "$(mk_node 300 false UNSTABLE "" "" "" "" MERGEABLE \
     "$(mk_checks "C:reconcile:IN_PROGRESS:null")")" > "$sb/resp_1.json"
run_reconciler "$sb" >/dev/null 2>&1
if grep -q -- '--add-label ready-to-merge' "$sb/calls"; then
  ok "reconcile in-progress is excluded from blocking checks; PR with no other blocking check gets ready-to-merge"
else
  notok "reconcile in-progress is excluded from blocking checks; PR with no other blocking check gets ready-to-merge"
  cat "$sb/calls" 2>/dev/null
fi

# ---- 24: non-reconcile FAILURE revokes a held ready-to-merge ----------
# The exclusion is specific to the reconciler's own job: every other non-passing
# check still blocks. A PR already holding the chip with a different job concluding
# FAILURE must have the chip removed, proving the exclusion is narrow.
sb="$(new_sandbox)"
_fail_check24="$(mk_checks "C:build:COMPLETED:FAILURE")"
mk_page false "" \
  "$(mk_node 320 false UNSTABLE "ready-to-merge" "" "" "" "" "$_fail_check24")" \
  "$(mk_node 420 false UNSTABLE "" "" "" "" "" "$_fail_check24")" > "$sb/resp_1.json"
run_reconciler "$sb" >/dev/null 2>&1
if grep -q '320 .*--remove-label ready-to-merge' "$sb/calls" \
   && ! grep -q -- '--add-label ready-to-merge' "$sb/calls"; then
  ok "non-reconcile FAILURE revokes a held ready-to-merge and withholds on an unlabelled PR"
else
  notok "non-reconcile FAILURE revokes a held ready-to-merge and withholds on an unlabelled PR"
  cat "$sb/calls" 2>/dev/null
fi

# ---- 25: non-reconcile in-progress blocks; chip not granted ----------
# A CheckRun whose conclusion is null (status IN_PROGRESS) is blocking when its
# name does not match the excluded reconciler job. The PR must NOT receive the
# chip while the check runs.
sb="$(new_sandbox)"
mk_page false "" \
  "$(mk_node 340 false UNSTABLE "" "" "" "" MERGEABLE \
     "$(mk_checks "C:build:IN_PROGRESS:null")")" > "$sb/resp_1.json"
run_reconciler "$sb" >/dev/null 2>&1
if ! grep -q -- '--add-label ready-to-merge' "$sb/calls"; then
  ok "non-reconcile in-progress (null conclusion) blocks; ready-to-merge not granted"
else
  notok "non-reconcile in-progress (null conclusion) blocks; ready-to-merge not granted"
  cat "$sb/calls" 2>/dev/null
fi

# ---- 26: all non-reconcile checks SUCCESS/NEUTRAL/SKIPPED → granted --------
# The three permitted passing conclusions must all be treated as passing by the
# individual-check predicate. A mix of SUCCESS, NEUTRAL, and SKIPPED with no
# other blocking check must grant ready-to-merge, proving each conclusion type
# is individually accepted.
sb="$(new_sandbox)"
_pass_checks="$(mk_checks "C:unit-tests:COMPLETED:SUCCESS,C:lint:COMPLETED:NEUTRAL,C:format:COMPLETED:SKIPPED")"
mk_page false "" \
  "$(mk_node 360 false CLEAN "" "" "" "" MERGEABLE "$_pass_checks")" > "$sb/resp_1.json"
run_reconciler "$sb" >/dev/null 2>&1
if grep -q -- '--add-label ready-to-merge' "$sb/calls"; then
  ok "all non-reconcile checks SUCCESS/NEUTRAL/SKIPPED are treated as passing; PR is granted ready-to-merge"
else
  notok "all non-reconcile checks SUCCESS/NEUTRAL/SKIPPED are treated as passing; PR is granted ready-to-merge"
  cat "$sb/calls" 2>/dev/null
fi

# ---- 27: null rollup (no checks on record) does not withhold ready-to-merge --
# A PR with no statusCheckRollup at all (never had a check run) is treated as
# having 0 blocking checks — it should not be withheld on the check-state clause.
# Uses mergeStateStatus=UNSTABLE to show that the predicate consults individual
# check counts, not the mergeStateStatus field.
sb="$(new_sandbox)"
mk_page false "" \
  "$(mk_node 370 false UNSTABLE "" "" "" "" MERGEABLE)" > "$sb/resp_1.json"
run_reconciler "$sb" >/dev/null 2>&1
if grep -q -- '--add-label ready-to-merge' "$sb/calls"; then
  ok "null statusCheckRollup (no checks on record) does not withhold ready-to-merge"
else
  notok "null statusCheckRollup (no checks on record) does not withhold ready-to-merge"
  cat "$sb/calls" 2>/dev/null
fi

# ---- 28: empty (non-null, zero-context) rollup does not withhold -----------
# A statusCheckRollup that exists but has no contexts (totalCount=0, nodes=[])
# must also not trigger the fail-closed truncation rule or count any blocking
# checks — it is indistinguishable from "all checks passed" and must grant.
sb="$(new_sandbox)"
_empty_checks="$(mk_checks "")"
mk_page false "" \
  "$(mk_node 380 false CLEAN "" "" "" "" "" "$_empty_checks")" > "$sb/resp_1.json"
run_reconciler "$sb" >/dev/null 2>&1
if grep -q -- '--add-label ready-to-merge' "$sb/calls"; then
  ok "empty statusCheckRollup (non-null, zero contexts) does not withhold ready-to-merge"
else
  notok "empty statusCheckRollup (non-null, zero contexts) does not withhold ready-to-merge"
  cat "$sb/calls" 2>/dev/null
fi

# ---- 29: truncated rollup → fail closed; chip not granted, held chip revoked --
# The rollup is present but totalCount (5) exceeds the fetched count (1), so the
# reconciler cannot rule out a blocking check in the unfetched tail. A PR already
# holding the chip must have it revoked, and an unlabelled PR must not receive it.
sb="$(new_sandbox)"
_trunc_checks="$(mk_checks "C:unit-tests:COMPLETED:SUCCESS")"
mk_page false "" \
  "$(mk_node 390 false CLEAN "ready-to-merge" "" "" "" "" "$_trunc_checks" 5)" \
  "$(mk_node 490 false CLEAN "" "" "" "" "" "$_trunc_checks" 5)" > "$sb/resp_1.json"
run_reconciler "$sb" >/dev/null 2>&1
if grep -q '390 .*--remove-label ready-to-merge' "$sb/calls" \
   && ! grep -q -- '--add-label ready-to-merge' "$sb/calls"; then
  ok "truncated rollup (totalCount > fetched) fails closed: held chip revoked, unlabelled PR not granted"
else
  notok "truncated rollup (totalCount > fetched) fails closed: held chip revoked, unlabelled PR not granted"
  cat "$sb/calls" 2>/dev/null
fi

# ---- 30: mergeable == CONFLICTING → conflicts applied, ready-to-merge withheld ---
# The conflict derivation comes from mergeable, not mergeStateStatus. All-green
# checks prove the check-state clause cannot override the conflict gate — even a PR
# with every check passing must never receive ready-to-merge when it is conflicted.
# The PR already holding ready-to-merge must have it revoked; the unlabelled PR
# must not receive it.
sb="$(new_sandbox)"
_green_30="$(mk_checks "C:unit-tests:COMPLETED:SUCCESS")"
mk_page false "" \
  "$(mk_node 400 false DIRTY "ready-to-merge" "" "" "" "" "$_green_30")" \
  "$(mk_node 410 false DIRTY "" "" "" "" "" "$_green_30")" > "$sb/resp_1.json"
run_reconciler "$sb" >/dev/null 2>&1
if grep -q '400 .*--remove-label ready-to-merge' "$sb/calls" \
   && grep -q '400 .*--add-label conflicts' "$sb/calls" \
   && grep -q '410 .*--add-label conflicts' "$sb/calls" \
   && ! grep -q -- '--add-label ready-to-merge' "$sb/calls"; then
  ok "mergeable==CONFLICTING: conflicts applied and ready-to-merge withheld even with green checks"
else
  notok "mergeable==CONFLICTING: conflicts applied and ready-to-merge withheld even with green checks"
  cat "$sb/calls" 2>/dev/null
fi

# ---- 31: conflicted draft still gets the conflicts chip ----------------------
# mergeStateStatus is a priority field that reports DRAFT on a draft PR, masking
# DIRTY. A conflicted draft would appear unconflicted through mergeStateStatus
# alone — which is why conflicts is derived from mergeable == CONFLICTING, not
# from mergeStateStatus == DIRTY. The draft is explicitly given mergeable=CONFLICTING
# while mergeStateStatus=DRAFT so the fixture mirrors the real API's behaviour.
sb="$(new_sandbox)"
mk_page false "" \
  "$(mk_node 420 true DRAFT "" "" "" "" CONFLICTING)" > "$sb/resp_1.json"
run_reconciler "$sb" >/dev/null 2>&1
if grep -q '420 .*--add-label conflicts' "$sb/calls"; then
  ok "conflicted draft (mergeable=CONFLICTING, mergeStateStatus=DRAFT) still gets the conflicts chip"
else
  notok "conflicted draft (mergeable=CONFLICTING, mergeStateStatus=DRAFT) still gets the conflicts chip"
  cat "$sb/calls" 2>/dev/null
fi

# ---- 32: draft with all-green checks → still not ready ----------------------
# A draft PR with every check concluding SUCCESS must not receive ready-to-merge.
# The draft gate is independent of the check-state clause: no combination of green
# checks can override it.  An existing held chip must be revoked, and an unlabelled
# draft must not receive one.
sb="$(new_sandbox)"
_green_32="$(mk_checks "C:unit-tests:COMPLETED:SUCCESS,C:lint:COMPLETED:SUCCESS")"
mk_page false "" \
  "$(mk_node 430 true CLEAN "ready-to-merge" "" "" "" "" "$_green_32")" \
  "$(mk_node 431 true CLEAN "" "" "" "" "" "$_green_32")" > "$sb/resp_1.json"
run_reconciler "$sb" >/dev/null 2>&1
if grep -q '430 .*--remove-label ready-to-merge' "$sb/calls" \
   && ! grep -q -- '--add-label ready-to-merge' "$sb/calls"; then
  ok "draft with all-green checks: held ready-to-merge revoked and not granted to unlabelled draft"
else
  notok "draft with all-green checks: held ready-to-merge revoked and not granted to unlabelled draft"
  cat "$sb/calls" 2>/dev/null
fi

# ---- 33: one unresolved thread + all-green checks → still not ready ---------
# An open review thread (isResolved=false) must block ready-to-merge even when
# every check concludes SUCCESS.  The thread-count gate is independent of the
# check-state clause.  An existing chip must be revoked; an unlabelled PR must not
# receive one.
sb="$(new_sandbox)"
_green_33="$(mk_checks "C:unit-tests:COMPLETED:SUCCESS")"
mk_page false "" \
  "$(mk_node 440 false CLEAN "ready-to-merge" "false:false" "" "" "" "$_green_33")" \
  "$(mk_node 441 false CLEAN "" "false:false" "" "" "" "$_green_33")" > "$sb/resp_1.json"
run_reconciler "$sb" >/dev/null 2>&1
if grep -q '440 .*--remove-label ready-to-merge' "$sb/calls" \
   && ! grep -q -- '--add-label ready-to-merge' "$sb/calls"; then
  ok "unresolved review thread + all-green checks: held ready-to-merge revoked and not granted to unlabelled PR"
else
  notok "unresolved review thread + all-green checks: held ready-to-merge revoked and not granted to unlabelled PR"
  cat "$sb/calls" 2>/dev/null
fi

# ---- 34: failing StatusContext (legacy commit status) blocks ready-to-merge --
# A StatusContext whose state is not SUCCESS must count as a blocking check.
# This proves the non-CheckRun union member of the rollup is handled: a PR
# carrying only a FAILURE StatusContext must not receive ready-to-merge, and
# an already-labelled PR must have the chip revoked.
sb="$(new_sandbox)"
_fail_sc="$(mk_checks "S:ci/jenkins/branch:FAILURE")"
mk_page false "" \
  "$(mk_node 450 false CLEAN "ready-to-merge" "" "" "" "" "$_fail_sc")" \
  "$(mk_node 451 false CLEAN "" "" "" "" "" "$_fail_sc")" > "$sb/resp_1.json"
run_reconciler "$sb" >/dev/null 2>&1
if grep -q '450 .*--remove-label ready-to-merge' "$sb/calls" \
   && ! grep -q -- '--add-label ready-to-merge' "$sb/calls"; then
  ok "failing StatusContext blocks ready-to-merge: held chip revoked, unlabelled PR not granted"
else
  notok "failing StatusContext blocks ready-to-merge: held chip revoked, unlabelled PR not granted"
  cat "$sb/calls" 2>/dev/null
fi

# ---- 35: BEHIND withholds readiness — checks never ran against the current base --
# The rollup hangs off the PR's HEAD COMMIT, so it is base-agnostic by construction:
# it records that the checks passed, never which base they were merged against.
# Retargeting a PR arrives as `edited`, which this reconciler observes but neither
# check producer does (ci.yml uses the default pull_request activity types;
# pr-preview.yml selects opened/synchronize/reopened), so the head commit keeps its
# green rollup from the OLD base and blocking_checks reads 0.
#
# BEHIND is the one base-relative signal GitHub gives us: the head ref is out of date
# with the base, so the checks on record cannot have tested the merge result. Treat it
# like a blocking check — withhold, and revoke a chip already held.
sb="$(new_sandbox)"
_green_35="$(mk_checks "C:gate:COMPLETED:SUCCESS")"
mk_page false "" \
  "$(mk_node 460 false BEHIND "ready-to-merge" "" "" "" MERGEABLE "$_green_35")" \
  "$(mk_node 461 false BEHIND "" "" "" "" MERGEABLE "$_green_35")" > "$sb/resp_1.json"
run_reconciler "$sb" >/dev/null 2>&1
if grep -q '460 .*--remove-label ready-to-merge' "$sb/calls" \
   && ! grep -q -- '--add-label ready-to-merge' "$sb/calls"; then
  ok "BEHIND withholds ready-to-merge even with green checks: held chip revoked, unlabelled PR not granted"
else
  notok "BEHIND withholds ready-to-merge even with green checks: held chip revoked, unlabelled PR not granted"
  cat "$sb/calls" 2>/dev/null
fi

# ---- 36: BEHIND does NOT attract the conflicts chip --------------------------
# A PR that is merely out of date is not conflicted. Withholding readiness must not
# tip over into asserting a conflict that `mergeable` does not report — the same
# both-directions discipline the UNKNOWN path already follows.
sb="$(new_sandbox)"
mk_page false "" \
  "$(mk_node 470 false BEHIND "" "" "" "" MERGEABLE "$_green_35")" > "$sb/resp_1.json"
run_reconciler "$sb" >/dev/null 2>&1
if ! grep -q -- '--add-label conflicts' "$sb/calls"; then
  ok "BEHIND does not attract the conflicts chip"
else
  notok "BEHIND does not attract the conflicts chip"
  cat "$sb/calls" 2>/dev/null
fi

printf '\n%s passed, %s failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]

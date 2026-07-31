#!/usr/bin/env bash
# Self-test for pr_readiness_labels.sh: exercises the reconciler's contract
# against a fake `gh` — no network, no GitHub, nothing mutated anywhere.
#
# The contract under test is a PREDICATE plus an ASYMMETRY. `ready-to-merge`
# means "a human can merge this now": not a draft, mergeStateStatus CLEAN
# (mergeable AND checks green), and zero LIVE review findings. A finding is
# live when it is unresolved AND not outdated — GitHub marks a thread outdated
# once a later push moved the code it pointed at, and this repo has never
# resolved a thread, so `isOutdated` is the signal that carries the
# information. `conflicts` means mergeStateStatus DIRTY and nothing else.
#
#    1-2. the happy path adds the chip, and a live finding withholds it
#    3-4. an outdated or resolved finding does NOT withhold it
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

mk_labels() {
  local spec="${1:-}" out="" l
  [ -z "$spec" ] && { printf '[]'; return; }
  local IFS=','
  for l in $spec; do out+="{\"name\":\"$l\"},"; done
  printf '[%s]' "${out%,}"
}

csv_len() {
  if [ -z "${1:-}" ]; then printf '0'; return; fi
  printf '%s' "$(( $(printf '%s' "$1" | tr -cd ',' | wc -c) + 1 ))"
}

# mk_node <number> <isDraft> <mergeStateStatus> <labels-csv> <threads-csv> \
#         [threads-totalCount] [labels-totalCount]
#
# `mergeable` is derived so a fixture cannot describe a state GitHub would never
# return (UNKNOWN status with a computed mergeable, say). Each connection's
# totalCount defaults to the number of nodes supplied; the overrides exist to
# simulate a TRUNCATED connection, where totalCount exceeds what was fetched.
mk_node() {
  local n="$1" draft="$2" state="$3" labels="${4:-}" threads="${5:-}"
  local tt="${6:-}" lt="${7:-}" mergeable tcount lcount
  case "$state" in
    UNKNOWN) mergeable=UNKNOWN ;;
    DIRTY)   mergeable=CONFLICTING ;;
    *)       mergeable=MERGEABLE ;;
  esac
  tcount="$(csv_len "$threads")"
  lcount="$(csv_len "$labels")"
  if [ -n "$tt" ]; then tcount="$tt"; fi
  if [ -n "$lt" ]; then lcount="$lt"; fi
  printf '{"number":%s,"isDraft":%s,"mergeable":"%s","mergeStateStatus":"%s","labels":{"totalCount":%s,"nodes":%s},"reviewThreads":{"totalCount":%s,"nodes":%s}}' \
    "$n" "$draft" "$mergeable" "$state" \
    "$lcount" "$(mk_labels "$labels")" "$tcount" "$(mk_threads "$threads")"
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

# ---- 3: only an OUTDATED finding -> still ready ----------------------------
sb="$(new_sandbox)"
mk_page false "" "$(mk_node 158 false CLEAN "" "false:true")" > "$sb/resp_1.json"
run_reconciler "$sb" >/dev/null 2>&1
if grep -q -- '--add-label ready-to-merge' "$sb/calls"; then
  ok "an outdated finding does not withhold ready-to-merge"
else
  notok "an outdated finding does not withhold ready-to-merge"
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
sb="$(new_sandbox)"
mk_page false "" \
  "$(mk_node 162 false UNSTABLE "ready-to-merge" "")" \
  "$(mk_node 262 false UNSTABLE "" "")" > "$sb/resp_1.json"
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

printf '\n%s passed, %s failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]

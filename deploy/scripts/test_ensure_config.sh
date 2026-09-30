#!/usr/bin/env bash
# Self-test for ensure_config (lib.sh): exercises the config-provisioning
# contract against throwaway local fixture repos — no network, never touches
# the real config/ checkout. Cases mirror the deploy-config-provisioning spec:
#    1. fresh clone at the pin
#    2. clean-tree convergence to the pin
#    3. untracked-only dirt does NOT block convergence (untracked preserved)
#    4. tracked edits at the pin: untouched, deploy proceeds, paths named
#    5. tracked edits off the pin: ABORTS with ahead/behind drift + force hint
#    6. CONFIG_FORCE=1 on (5): stashes, converges, provenance labels the stash
#    7. re-pointed REMOTE tag on a provisioned host aborts naming both ids
#    8. wrong CONFIG_SHA on a fresh host aborts naming both ids
#    9. provenance recorded on every run
#   10. wrong path type (agents as a file) aborts
#   11. a pin bump in one deployment's row leaves the other deployment's pin unchanged
#   12. a deployment with no pin row sources cleanly, then ensure_config aborts
#   13. an environment pin (CONFIG_REF/CONFIG_SHA) overrides the table for any name
#   14-15. an environment pin with only one of the two keys aborts before provisioning
# Run: bash deploy/scripts/test_ensure_config.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB="$SCRIPT_DIR/lib.sh"
PASS=0; FAIL=0
ok()    { printf 'ok - %s\n' "$1"; PASS=$((PASS + 1)); }
notok() { printf 'not ok - %s\n' "$1"; FAIL=$((FAIL + 1)); }

# git with a fixed identity so the fixture works on hosts with no git config.
g() { git -c user.email=test@test.invalid -c user.name=ensure-config-test -C "$@"; }

TESTROOT="$(mktemp -d)"
trap 'rm -rf "$TESTROOT"' EXIT

# Fixture: a bare "remote" whose annotated pin tag points one commit behind
# main, so convergence and drift are observable. Pin sha lands in $sb/pin_sha.
make_fixture() { # $1 = sandbox dir
  local sb="$1" seed="$1/seed"
  mkdir -p "$seed"
  git init -q -b main "$seed"
  mkdir -p "$seed/lists" "$seed/environments" "$seed/agents"
  echo "https://example.org/docs" > "$seed/lists/sources.list"
  echo "name: test" > "$seed/environments/dev.yaml"
  echo "test agent" > "$seed/agents/test.md"
  g "$seed" add -A
  g "$seed" commit -qm "pinned content"
  g "$seed" tag -a deploy-pin-test -m "test pin"
  g "$seed" rev-parse HEAD > "$sb/pin_sha"
  echo "https://example.org/later" >> "$seed/lists/sources.list"
  g "$seed" commit -qam "after the pin"
  git clone -q --bare "$seed" "$sb/remote.git"
}

# Run ensure_config in a clean subshell with the fixture env. Combined output
# lands in $sb/out; echoes the exit code.
run_ensure() { # $1=sandbox, rest = extra env VAR=val pairs
  local sb="$1"; shift
  local ec=0
  env "$@" \
      CONFIG_DIR="$sb/config" \
      CONFIG_REPO="file://$sb/remote.git" \
      CONFIG_REF=deploy-pin-test \
      CONFIG_SHA="$(cat "$sb/pin_sha")" \
      bash -c "source '$LIB'; ensure_config" > "$sb/out" 2>&1 || ec=$?
  echo "$ec"
}

# Run ensure_config, then print the provenance it EXPORTS. The exports are what
# reach config_seed (and thus the status board); the log lines alone never leave
# the deploy output.
run_ensure_exports() { # $1=sandbox, rest = extra env VAR=val pairs
  local sb="$1"; shift
  local ec=0
  env "$@" \
      CONFIG_DIR="$sb/config" \
      CONFIG_REPO="file://$sb/remote.git" \
      CONFIG_REF=deploy-pin-test \
      CONFIG_SHA="$(cat "$sb/pin_sha")" \
      bash -c "source '$LIB'; ensure_config >/dev/null 2>&1; \
printf 'EXPORT_REF=[%s]\n' \"\$ARCHI_CONFIG_REF\"; \
printf 'EXPORT_SHA=[%s]\n' \"\$ARCHI_CONFIG_SHA\"; \
printf 'EXPORT_HEAD=[%s]\n' \"\$ARCHI_CONFIG_HEAD\"; \
printf 'EXPORT_MATCHED=[%s]\n' \"\$ARCHI_CONFIG_PIN_MATCHED\"; \
printf 'EXPORT_DIRTY=[%s]\n' \"\$ARCHI_CONFIG_DIRTY_PATHS\"" > "$sb/exports" 2>&1 || ec=$?
  echo "$ec"
}

# --- 1: fresh clone -----------------------------------------------------------
sb="$TESTROOT/fresh"; mkdir -p "$sb"; make_fixture "$sb"
ec="$(run_ensure "$sb")"
if [ "$ec" = 0 ] \
   && [ "$(g "$sb/config" rev-parse HEAD)" = "$(cat "$sb/pin_sha")" ] \
   && [ -f "$sb/config/lists/sources.list" ]; then
  ok "1 fresh host: clones at the pin"
else
  notok "1 fresh host (ec=$ec)"; cat "$sb/out" || true
fi

# --- 2: clean-tree convergence --------------------------------------------------
sb="$TESTROOT/clean"; mkdir -p "$sb"; make_fixture "$sb"
git clone -q "file://$sb/remote.git" "$sb/config"     # HEAD = main, ahead of pin
ec="$(run_ensure "$sb")"
if [ "$ec" = 0 ] && [ "$(g "$sb/config" rev-parse HEAD)" = "$(cat "$sb/pin_sha")" ]; then
  ok "2 clean tree: converges to the pin"
else
  notok "2 clean tree (ec=$ec)"; cat "$sb/out" || true
fi

# --- 3: untracked-only dirt converges anyway -----------------------------------
sb="$TESTROOT/untracked"; mkdir -p "$sb"; make_fixture "$sb"
git clone -q "file://$sb/remote.git" "$sb/config"     # stale HEAD (main != pin)
echo "local bank" > "$sb/config/local-bank.json"
ec="$(run_ensure "$sb")"
if [ "$ec" = 0 ] \
   && [ "$(g "$sb/config" rev-parse HEAD)" = "$(cat "$sb/pin_sha")" ] \
   && [ -f "$sb/config/local-bank.json" ]; then
  ok "3 untracked-only: converges to pin, untracked file preserved"
else
  notok "3 untracked-only convergence (ec=$ec)"; cat "$sb/out" || true
fi

# --- 4: tracked edits AT the pin proceed, untouched ------------------------------
sb="$TESTROOT/dirty-at-pin"; mkdir -p "$sb"; make_fixture "$sb"
git clone -q "file://$sb/remote.git" "$sb/config"
g "$sb/config" checkout -q "$(cat "$sb/pin_sha")"     # HEAD == pin
echo "LIVE EDIT" >> "$sb/config/lists/sources.list"
sum_before="$(sha256sum "$sb/config/lists/sources.list" | cut -d' ' -f1)"
ec="$(run_ensure "$sb")"
if [ "$ec" = 0 ] \
   && [ "$(sha256sum "$sb/config/lists/sources.list" | cut -d' ' -f1)" = "$sum_before" ] \
   && grep -q "sources.list" "$sb/out"; then
  ok "4 tracked edits at pin: untouched, deploy proceeds, paths named"
else
  notok "4 tracked edits at pin (ec=$ec)"; cat "$sb/out" || true
fi

# --- 5: tracked edits OFF the pin abort -----------------------------------------
sb="$TESTROOT/dirty-off-pin"; mkdir -p "$sb"; make_fixture "$sb"
git clone -q "file://$sb/remote.git" "$sb/config"     # HEAD = main (ahead of pin)
echo "LIVE EDIT" >> "$sb/config/lists/sources.list"
sum_before="$(sha256sum "$sb/config/lists/sources.list" | cut -d' ' -f1)"
ec="$(run_ensure "$sb")"
if [ "$ec" != 0 ] \
   && [ "$(sha256sum "$sb/config/lists/sources.list" | cut -d' ' -f1)" = "$sum_before" ] \
   && grep -q "ahead 1" "$sb/out" \
   && grep -q "CONFIG_FORCE=1" "$sb/out"; then
  ok "5 tracked edits off pin: aborts with ahead/behind drift + force hint"
else
  notok "5 tracked edits off pin (ec=$ec)"; cat "$sb/out" || true
fi

# --- 6: CONFIG_FORCE=1 stashes and converges -------------------------------------
sb="$TESTROOT/force"; mkdir -p "$sb"; make_fixture "$sb"
git clone -q "file://$sb/remote.git" "$sb/config"
echo "LIVE EDIT" >> "$sb/config/lists/sources.list"
echo "bank" > "$sb/config/local-bank.json"
ec="$(run_ensure "$sb" CONFIG_FORCE=1)"
if [ "$ec" = 0 ] \
   && [ "$(g "$sb/config" rev-parse HEAD)" = "$(cat "$sb/pin_sha")" ] \
   && [ -n "$(g "$sb/config" stash list)" ] \
   && grep -q "stash pop" "$sb/out" \
   && grep -qi "stashed" "$sb/out" \
   && ! grep -A2 "provenance: dirty" "$sb/out" | grep -q "sources.list"; then
  ok "6 CONFIG_FORCE=1: converges, stash + hint, provenance labels stash (not live dirt)"
else
  notok "6 CONFIG_FORCE=1 (ec=$ec)"; cat "$sb/out" || true
fi

# --- 7: re-pointed REMOTE tag on a provisioned host aborts ------------------------
sb="$TESTROOT/repoint-remote"; mkdir -p "$sb"; make_fixture "$sb"
git clone -q "file://$sb/remote.git" "$sb/config"     # provisioned
main_sha="$(g "$sb/seed" rev-parse HEAD)"
g "$sb/remote.git" tag -d deploy-pin-test >/dev/null
g "$sb/remote.git" tag -a deploy-pin-test -m "tampered" "$main_sha"
ec="$(run_ensure "$sb")"
if [ "$ec" != 0 ] && grep -q "$main_sha" "$sb/out" && grep -q "$(cat "$sb/pin_sha")" "$sb/out"; then
  ok "7 re-pointed remote tag: aborts naming both commit ids"
else
  notok "7 re-pointed remote tag (ec=$ec)"; cat "$sb/out" || true
fi

# --- 8: wrong CONFIG_SHA on a fresh host aborts -----------------------------------
sb="$TESTROOT/wrong-sha"; mkdir -p "$sb"; make_fixture "$sb"
wrong_sha="$(g "$sb/seed" rev-parse HEAD)"
ec=0
env CONFIG_DIR="$sb/config" CONFIG_REPO="file://$sb/remote.git" \
    CONFIG_REF=deploy-pin-test CONFIG_SHA="$wrong_sha" \
    bash -c "source '$LIB'; ensure_config" > "$sb/out" 2>&1 || ec=$?
if [ "$ec" != 0 ] && grep -q "$wrong_sha" "$sb/out" && grep -q "$(cat "$sb/pin_sha")" "$sb/out"; then
  ok "8 wrong CONFIG_SHA: aborts naming both commit ids"
else
  notok "8 wrong CONFIG_SHA (ec=$ec)"; cat "$sb/out" || true
fi

# --- 9: provenance on every run ----------------------------------------------------
sb="$TESTROOT/provenance"; mkdir -p "$sb"; make_fixture "$sb"
ec="$(run_ensure "$sb")"
if [ "$ec" = 0 ] && grep -q "provenance" "$sb/out" && grep -q "$(cat "$sb/pin_sha")" "$sb/out"; then
  ok "9 provenance: HEAD + pin-match recorded"
else
  notok "9 provenance (ec=$ec)"; cat "$sb/out" || true
fi

# --- 10: wrong path type aborts ------------------------------------------------------
sb="$TESTROOT/wrong-type"; mkdir -p "$sb" "$sb/seed"
git init -q -b main "$sb/seed"
mkdir -p "$sb/seed/lists" "$sb/seed/environments"
echo "x" > "$sb/seed/lists/sources.list"
echo "name: t" > "$sb/seed/environments/dev.yaml"
echo "not a directory" > "$sb/seed/agents"        # agents as a FILE
g "$sb/seed" add -A; g "$sb/seed" commit -qm "wrong type"
g "$sb/seed" tag -a deploy-pin-test -m pin
g "$sb/seed" rev-parse HEAD > "$sb/pin_sha"
git clone -q --bare "$sb/seed" "$sb/remote.git"
ec="$(run_ensure "$sb")"
if [ "$ec" != 0 ] && grep -q "agents" "$sb/out"; then
  ok "10 wrong path type: aborts naming the bad path"
else
  notok "10 wrong path type (ec=$ec)"; cat "$sb/out" || true
fi

# --- 11: provenance is EXPORTED, not only logged ------------------------------------
# CONFIG_REF is otherwise a shell variable that never enters a container; these
# exports are the only path by which the running deployment learns its own pin.
sb="$TESTROOT/exports-clean"; mkdir -p "$sb"; make_fixture "$sb"
ec="$(run_ensure_exports "$sb")"
pin="$(cat "$sb/pin_sha")"
if [ "$ec" = 0 ] \
   && grep -q "EXPORT_REF=\[deploy-pin-test\]" "$sb/exports" \
   && grep -q "EXPORT_SHA=\[$pin\]" "$sb/exports" \
   && grep -q "EXPORT_HEAD=\[$pin\]" "$sb/exports" \
   && grep -q "EXPORT_MATCHED=\[yes\]" "$sb/exports" \
   && grep -q "EXPORT_DIRTY=\[\]" "$sb/exports"; then
  ok "11 exports: ref/sha/head/matched exported, no dirt on a clean deploy"
else
  notok "11 exports on clean deploy (ec=$ec)"; cat "$sb/exports" || true
fi

# --- 12: live edits AT the pin export matched=yes WITH dirty paths -------------------
# The subtle case the status board must not misreport: the deploy is permitted,
# HEAD equals the pin, and yet the config running is NOT the pinned config.
sb="$TESTROOT/exports-live-edit"; mkdir -p "$sb"; make_fixture "$sb"
git clone -q "file://$sb/remote.git" "$sb/config"
g "$sb/config" checkout -q "$(cat "$sb/pin_sha")"
echo "LIVE EDIT" >> "$sb/config/lists/sources.list"
ec="$(run_ensure_exports "$sb")"
if [ "$ec" = 0 ] \
   && grep -q "EXPORT_MATCHED=\[yes\]" "$sb/exports" \
   && grep -q "sources.list" "$sb/exports"; then
  ok "12 exports: live edits at the pin export matched=yes AND the dirty path"
else
  notok "12 exports with live edits at the pin (ec=$ec)"; cat "$sb/exports" || true
fi

# --- 13: untracked-only dirt exports NO dirty paths ---------------------------------
# Untracked files are not a live edit — the deploy converges to the pin around
# them — so they must not raise the board's live-edited warning.
sb="$TESTROOT/exports-untracked"; mkdir -p "$sb"; make_fixture "$sb"
git clone -q "file://$sb/remote.git" "$sb/config"
echo "local bank" > "$sb/config/local-bank.json"
ec="$(run_ensure_exports "$sb")"
if [ "$ec" = 0 ] \
   && grep -q "EXPORT_MATCHED=\[yes\]" "$sb/exports" \
   && grep -q "EXPORT_DIRTY=\[\]" "$sb/exports"; then
  ok "13 exports: untracked-only dirt is not reported as a live edit"
else
  notok "13 exports with untracked-only dirt (ec=$ec)"; cat "$sb/exports" || true
fi


# Print the pin a copy of lib.sh resolves for one deployment, with no pin in the
# environment. The copy lives in a sandbox dir, so no real host.env applies.
resolve_pin() { # $1=lib.sh path, $2=deployment name
  env -u CONFIG_REF -u CONFIG_SHA DEPLOYMENT="$2" \
    bash -c "source '$1'; printf '%s %s\n' \"\$CONFIG_REF\" \"\$CONFIG_SHA\""
}

# --- 14: a pin bump for one deployment leaves the other unchanged -------------------
sb="$TESTROOT/per-deployment"; mkdir -p "$sb/scripts"
new_sha=0123456789abcdef0123456789abcdef01234567
sed -E "s|^( +claw\) ).*|\1_pin_ref=deploy-pin-bumped; _pin_sha=$new_sha ;;|" \
  "$LIB" > "$sb/scripts/lib.sh"
dev_before="$(resolve_pin "$LIB" dev)"
if ! cmp -s "$LIB" "$sb/scripts/lib.sh"; then
  claw_after="$(resolve_pin "$sb/scripts/lib.sh" claw)"
  dev_after="$(resolve_pin "$sb/scripts/lib.sh" dev)"
else
  claw_after="(no claw row in the pin table)"; dev_after=""
fi
if [ "$claw_after" = "deploy-pin-bumped $new_sha" ] && [ "$dev_after" = "$dev_before" ] \
   && [ -n "${dev_before% }" ]; then
  ok "14 per-deployment pin: a claw bump moves claw only"
else
  notok "14 per-deployment pin: claw='$claw_after' dev before='$dev_before' after='$dev_after'"
fi

# --- 15: a deployment with no pin row aborts at provisioning, not at source ---------
sb="$TESTROOT/no-row"; mkdir -p "$sb"; make_fixture "$sb"
ec=0
env -u CONFIG_REF -u CONFIG_SHA DEPLOYMENT=scratch \
    CONFIG_DIR="$sb/config" CONFIG_REPO="file://$sb/remote.git" \
    bash -c "source '$LIB' && echo SOURCED && ensure_config" > "$sb/out" 2>&1 || ec=$?
if [ "$ec" != 0 ] && grep -q SOURCED "$sb/out" && grep -q "scratch" "$sb/out" \
   && grep -q "no config pin" "$sb/out" && [ ! -e "$sb/config" ]; then
  ok "15 no pin row: sources cleanly, ensure_config aborts naming the deployment"
else
  notok "15 no pin row (ec=$ec)"; cat "$sb/out" || true
fi

# --- 16: an environment pin works for a deployment with no row ----------------------
sb="$TESTROOT/no-row-override"; mkdir -p "$sb"; make_fixture "$sb"
ec="$(run_ensure "$sb" DEPLOYMENT=scratch)"
if [ "$ec" = 0 ] && [ "$(g "$sb/config" rev-parse HEAD)" = "$(cat "$sb/pin_sha")" ]; then
  ok "16 environment pin: overrides the table for any deployment name"
else
  notok "16 environment pin (ec=$ec)"; cat "$sb/out" || true
fi

# --- 17-18: a one-key environment pin aborts; it never mixes with the table row -------
for partial in "CONFIG_REF=deploy-pin-test" "CONFIG_SHA=$new_sha"; do
  key="${partial%%=*}"
  sb="$TESTROOT/partial-$key"; mkdir -p "$sb"; make_fixture "$sb"
  ec=0
  env -u CONFIG_REF -u CONFIG_SHA DEPLOYMENT=dev "$partial" \
      CONFIG_DIR="$sb/config" CONFIG_REPO="file://$sb/remote.git" \
      bash -c "source '$LIB' && echo SOURCED && ensure_config" > "$sb/out" 2>&1 || ec=$?
  if [ "$ec" != 0 ] && grep -q SOURCED "$sb/out" && grep -q "only $key" "$sb/out" \
     && [ ! -e "$sb/config" ]; then
    ok "partial environment pin ($key only): aborts before provisioning"
  else
    notok "partial environment pin ($key only) (ec=$ec)"; cat "$sb/out" || true
  fi
done
printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]

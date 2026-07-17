#!/usr/bin/env bash
# Self-test for ensure_config (lib.sh): exercises the config-provisioning
# contract against a throwaway local fixture repo — no network, no touching the
# real config/ checkout. Cases mirror the deploy-config-provisioning spec:
#   1. fresh clone at the pin
#   2. clean-tree convergence to the pin
#   3. dirty tree untouched by default (+ pin-drift warning)
#   4. CONFIG_FORCE=1 stashes (never reset/clean)
#   5. re-pointed tag (SHA mismatch) aborts naming both ids
#   6. provenance record present in output
#   7. missing expected file aborts
# Run: bash deploy/fasrc-dev/scripts/test_ensure_config.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB="$SCRIPT_DIR/lib.sh"
PASS=0; FAIL=0
ok()   { printf 'ok - %s\n' "$1"; PASS=$((PASS + 1)); }
notok() { printf 'not ok - %s\n' "$1"; FAIL=$((FAIL + 1)); }

# git with a fixed identity so the fixture works on hosts with no git config.
g() { git -c user.email=test@test.invalid -c user.name=ensure-config-test -C "$@"; }

TESTROOT="$(mktemp -d)"
trap 'rm -rf "$TESTROOT"' EXIT

# Build one fixture: a "remote" bare repo whose pin tag points one commit
# behind main, so convergence is observable. Echoes the pin commit sha.
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

# Run ensure_config in a clean subshell with the fixture env. Captures combined
# output to $sb/out and echoes the exit code.
run_ensure() { # $1=sandbox $2=extra-env (optional "VAR=val ..." string)
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

# --- case 1: fresh clone -----------------------------------------------------
sb="$TESTROOT/fresh"; mkdir -p "$sb"; make_fixture "$sb"
ec="$(run_ensure "$sb")"
if [ "$ec" = 0 ] \
   && [ "$(g "$sb/config" rev-parse HEAD)" = "$(cat "$sb/pin_sha")" ] \
   && [ -f "$sb/config/lists/sources.list" ] \
   && [ -f "$sb/config/environments/dev.yaml" ] \
   && [ -d "$sb/config/agents" ]; then
  ok "fresh host: clones at the pin with expected files"
else
  notok "fresh host: clones at the pin (ec=$ec)"; cat "$sb/out" || true
fi

# --- case 2: clean-tree convergence -------------------------------------------
sb="$TESTROOT/clean"; mkdir -p "$sb"; make_fixture "$sb"
git clone -q "file://$sb/remote.git" "$sb/config"   # HEAD = main, ahead of pin
ec="$(run_ensure "$sb")"
if [ "$ec" = 0 ] && [ "$(g "$sb/config" rev-parse HEAD)" = "$(cat "$sb/pin_sha")" ]; then
  ok "clean tree: converges to the pin"
else
  notok "clean tree: converges to the pin (ec=$ec)"; cat "$sb/out" || true
fi

# --- case 3: dirty tree untouched by default (+ drift warning) ---------------
sb="$TESTROOT/dirty"; mkdir -p "$sb"; make_fixture "$sb"
git clone -q "file://$sb/remote.git" "$sb/config"
echo "LIVE EDIT" >> "$sb/config/lists/sources.list"
echo "untracked bank" > "$sb/config/local-bank.json"
sum_tracked="$(sha256sum "$sb/config/lists/sources.list" | cut -d' ' -f1)"
head_before="$(g "$sb/config" rev-parse HEAD)"
ec="$(run_ensure "$sb")"
if [ "$ec" = 0 ] \
   && [ "$(sha256sum "$sb/config/lists/sources.list" | cut -d' ' -f1)" = "$sum_tracked" ] \
   && [ -f "$sb/config/local-bank.json" ] \
   && [ "$(g "$sb/config" rev-parse HEAD)" = "$head_before" ] \
   && grep -q "sources.list" "$sb/out" \
   && grep -Eq "drift|behind" "$sb/out"; then
  ok "dirty tree: untouched, deploy proceeds, warning names paths + pin drift"
else
  notok "dirty tree: untouched with drift warning (ec=$ec)"; cat "$sb/out" || true
fi

# --- case 4: CONFIG_FORCE=1 stashes, never destroys ---------------------------
sb="$TESTROOT/force"; mkdir -p "$sb"; make_fixture "$sb"
git clone -q "file://$sb/remote.git" "$sb/config"
echo "LIVE EDIT" >> "$sb/config/lists/sources.list"
echo "untracked bank" > "$sb/config/local-bank.json"
ec="$(run_ensure "$sb" CONFIG_FORCE=1)"
if [ "$ec" = 0 ] \
   && [ "$(g "$sb/config" rev-parse HEAD)" = "$(cat "$sb/pin_sha")" ] \
   && [ -n "$(g "$sb/config" stash list)" ] \
   && grep -q "stash pop" "$sb/out"; then
  ok "CONFIG_FORCE=1: converges, edits stashed, recovery hint printed"
else
  notok "CONFIG_FORCE=1: stash + converge (ec=$ec)"; cat "$sb/out" || true
fi

# --- case 5: re-pointed tag aborts --------------------------------------------
sb="$TESTROOT/repoint"; mkdir -p "$sb"; make_fixture "$sb"
wrong_sha="$(g "$sb/seed" rev-parse HEAD)"   # main tip != pin commit
ec=0
env CONFIG_DIR="$sb/config" CONFIG_REPO="file://$sb/remote.git" \
    CONFIG_REF=deploy-pin-test CONFIG_SHA="$wrong_sha" \
    bash -c "source '$LIB'; ensure_config" > "$sb/out" 2>&1 || ec=$?
if [ "$ec" != 0 ] \
   && grep -q "$wrong_sha" "$sb/out" \
   && grep -q "$(cat "$sb/pin_sha")" "$sb/out"; then
  ok "pin mismatch: aborts naming both commit ids"
else
  notok "pin mismatch: aborts naming both ids (ec=$ec)"; cat "$sb/out" || true
fi

# --- case 6: provenance recorded on every run ---------------------------------
sb="$TESTROOT/provenance"; mkdir -p "$sb"; make_fixture "$sb"
ec="$(run_ensure "$sb")"
if [ "$ec" = 0 ] \
   && grep -q "provenance" "$sb/out" \
   && grep -q "$(cat "$sb/pin_sha")" "$sb/out"; then
  ok "provenance: HEAD + pin-match recorded in deploy output"
else
  notok "provenance: recorded (ec=$ec)"; cat "$sb/out" || true
fi

# --- case 7: missing expected file aborts --------------------------------------
sb="$TESTROOT/missing"; mkdir -p "$sb" "$sb/seed"
git init -q -b main "$sb/seed"
mkdir -p "$sb/seed/environments" "$sb/seed/agents"   # no lists/sources.list
echo "name: test" > "$sb/seed/environments/dev.yaml"
echo "agent" > "$sb/seed/agents/test.md"
g "$sb/seed" add -A; g "$sb/seed" commit -qm "incomplete"
g "$sb/seed" tag -a deploy-pin-test -m pin
g "$sb/seed" rev-parse HEAD > "$sb/pin_sha"
git clone -q --bare "$sb/seed" "$sb/remote.git"
ec="$(run_ensure "$sb")"
if [ "$ec" != 0 ] && grep -q "sources.list" "$sb/out"; then
  ok "incomplete checkout: aborts naming the missing path"
else
  notok "incomplete checkout: aborts (ec=$ec)"; cat "$sb/out" || true
fi

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]

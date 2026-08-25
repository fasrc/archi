#!/usr/bin/env bash
# Self-test for the per-host override contract in lib.sh — exercised against a
# COPY of lib.sh in a temporary fixture tree and a fake `archi` binary, so it
# reads no real config, writes nothing into the working tree, renders no
# compose, and starts no container.
#
# host.env is DATA, not code (adversarial-review round 1): only KEY=VALUE lines
# for DEPLOYMENT / CONFIG / GPU_IDS are accepted, a value applies only when the
# variable is not already set (command line always wins), and anything else
# aborts the deploy before it touches anything.
#    1. no host.env: DEPLOYMENT=dev, CONFIG=deploy/fasrc-dev/config.yaml
#    2. a missing host.env is not an error
#    3. host.env DEPLOYMENT=claw reaches `archi create --name`
#    4. host.env CONFIG=... reaches `--config`
#    5. a command-line environment variable beats host.env
#    6. host.env GPU_IDS= (empty) still passes no --gpu-ids (the no-colon
#       ${GPU_IDS-} keeps distinguishing unset from empty)
#    7. a key outside the allowlist (e.g. CONFIG_SHA) aborts the deploy
#    8. a non-assignment line aborts AND is never executed
#    9. comments and blank lines are accepted
#
# Run: bash deploy/fasrc-dev/scripts/test_host_env.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PASS=0; FAIL=0
ok()    { printf 'ok - %s\n' "$1"; PASS=$((PASS + 1)); }
notok() { printf 'not ok - %s\n' "$1"; FAIL=$((FAIL + 1)); }

TESTROOT="$(mktemp -d)"
trap 'rm -rf "$TESTROOT"' EXIT

# Fixture repo tree: lib.sh is COPIED in, so the SCRIPT_DIR it computes points
# at the fixture and the host.env this test writes never touches the real tree.
FIXSCRIPTS="$TESTROOT/repo/deploy/fasrc-dev/scripts"
mkdir -p "$FIXSCRIPTS"
cp "$SCRIPT_DIR/lib.sh" "$FIXSCRIPTS/lib.sh"

# Fake `archi` that records the argv it was called with and exits clean.
mkdir -p "$TESTROOT/bin"
cat > "$TESTROOT/bin/archi" <<EOF
#!/usr/bin/env bash
printf '%s\n' "\$*" > "$TESTROOT/argv"
exit 0
EOF
chmod +x "$TESTROOT/bin/archi"

# Invoke archi_deploy in a subshell with the fake archi on PATH and the
# preflight/provisioning steps stubbed out — this test is about identity only.
run_deploy() { # env assignments passed as "VAR=value" args
  : > "$TESTROOT/argv"
  env "$@" PATH="$TESTROOT/bin:$PATH" bash -c '
    source "'"$FIXSCRIPTS"'/lib.sh"
    require_files() { :; }
    ensure_config() { :; }
    check_llm()     { :; }
    archi_deploy >/dev/null 2>&1
  '
}

# --- 1 + 2: no host.env — reserved defaults, and no error --------------------
rm -f "$FIXSCRIPTS/host.env"
if run_deploy HOST_ENV_ABSENT=1; then
  ok "2 missing host.env is not an error"
else
  notok "2 missing host.env must not be an error"
fi
argv="$(cat "$TESTROOT/argv")"
if [[ "$argv" == *"--name dev"* ]] && [[ "$argv" == *"--config deploy/fasrc-dev/config.yaml"* ]]; then
  ok "1 no host.env: DEPLOYMENT=dev, CONFIG=deploy/fasrc-dev/config.yaml"
else
  notok "1 no host.env should keep the reserved defaults, got: $argv"
fi

# --- 3: host.env renames the deployment --------------------------------------
printf 'DEPLOYMENT=claw\n' > "$FIXSCRIPTS/host.env"
run_deploy HOST_ENV_SET=1
argv="$(cat "$TESTROOT/argv")"
if [[ "$argv" == *"--name claw"* ]]; then
  ok "3 host.env DEPLOYMENT=claw reaches archi create --name"
else
  notok "3 host.env DEPLOYMENT should reach --name, got: $argv"
fi

# --- 4: host.env overrides CONFIG ---------------------------------------------
printf 'CONFIG=deploy/fasrc-dev/claw.yaml\n' > "$FIXSCRIPTS/host.env"
run_deploy HOST_ENV_SET=1
argv="$(cat "$TESTROOT/argv")"
if [[ "$argv" == *"--config deploy/fasrc-dev/claw.yaml"* ]]; then
  ok "4 host.env CONFIG reaches --config"
else
  notok "4 host.env CONFIG should reach --config, got: $argv"
fi

# --- 5: the command line always beats host.env --------------------------------
printf 'DEPLOYMENT=claw\n' > "$FIXSCRIPTS/host.env"
run_deploy DEPLOYMENT=other
argv="$(cat "$TESTROOT/argv")"
if [[ "$argv" == *"--name other"* ]]; then
  ok "5 command-line DEPLOYMENT beats host.env"
else
  notok "5 command-line env must beat host.env, got: $argv"
fi

# --- 6: GPU_IDS= (empty) through host.env still disables the flag -------------
printf 'GPU_IDS=\n' > "$FIXSCRIPTS/host.env"
run_deploy HOST_ENV_SET=1
argv="$(cat "$TESTROOT/argv")"
if [ -n "$argv" ] && [[ "$argv" != *"--gpu-ids"* ]]; then
  ok "6 host.env GPU_IDS= (empty): no --gpu-ids"
else
  notok "6 host.env GPU_IDS= should pass no --gpu-ids, got: $argv"
fi

# --- 7: a key outside the allowlist aborts ------------------------------------
printf 'CONFIG_SHA=deadbeef\n' > "$FIXSCRIPTS/host.env"
if run_deploy HOST_ENV_SET=1 2>/dev/null; then
  notok "7 an unsupported key (CONFIG_SHA) must abort the deploy"
else
  ok "7 unsupported key aborts the deploy"
fi

# --- 8: a non-assignment line aborts and is never executed --------------------
CANARY="$TESTROOT/canary"
: > "$CANARY"
printf 'rm -f %s\n' "$CANARY" > "$FIXSCRIPTS/host.env"
if run_deploy HOST_ENV_SET=1 2>/dev/null; then
  notok "8 a non-assignment line must abort the deploy"
else
  if [ -f "$CANARY" ]; then
    ok "8 non-assignment line aborts and is NOT executed"
  else
    notok "8 host.env content was EXECUTED (canary deleted)"
  fi
fi

# --- 9: comments and blank lines are fine --------------------------------------
printf '# per-host identity\n\nDEPLOYMENT=claw\n' > "$FIXSCRIPTS/host.env"
run_deploy HOST_ENV_SET=1
argv="$(cat "$TESTROOT/argv")"
if [[ "$argv" == *"--name claw"* ]]; then
  ok "9 comments and blank lines are accepted"
else
  notok "9 comments/blank lines should parse, got: $argv"
fi

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]

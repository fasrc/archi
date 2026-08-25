#!/usr/bin/env bash
# Self-test for the per-host override contract in lib.sh — exercised against a
# COPY of lib.sh in a temporary fixture tree and a fake `archi` binary, so it
# reads no real config, writes nothing into the working tree, renders no
# compose, and starts no container.
#    1. no host.env: DEPLOYMENT=dev, CONFIG=deploy/fasrc-dev/config.yaml
#    2. a missing host.env is not an error
#    3. host.env DEPLOYMENT (`:=` form) reaches `archi create --name`
#    4. host.env CONFIG (`:=` form) reaches `--config`
#    5. a command-line environment variable beats a `:=`-style host.env
#    6. a PLAIN assignment in host.env beats the command line — the documented
#       tradeoff of sourcing before the defaults, pinned here so changing it is
#       a deliberate red-test-first decision rather than an accident
#    7. host.env GPU_IDS="" still passes no --gpu-ids (the no-colon ${GPU_IDS-}
#       keeps distinguishing unset from empty through a host.env)
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

# --- 3: host.env renames the deployment (`:=` form) --------------------------
printf ': "${DEPLOYMENT:=claw}"\n' > "$FIXSCRIPTS/host.env"
run_deploy HOST_ENV_SET=1
argv="$(cat "$TESTROOT/argv")"
if [[ "$argv" == *"--name claw"* ]]; then
  ok "3 host.env DEPLOYMENT=claw reaches archi create --name"
else
  notok "3 host.env DEPLOYMENT should reach --name, got: $argv"
fi

# --- 4: host.env overrides CONFIG (`:=` form) ---------------------------------
printf ': "${CONFIG:=deploy/fasrc-dev/claw.yaml}"\n' > "$FIXSCRIPTS/host.env"
run_deploy HOST_ENV_SET=1
argv="$(cat "$TESTROOT/argv")"
if [[ "$argv" == *"--config deploy/fasrc-dev/claw.yaml"* ]]; then
  ok "4 host.env CONFIG reaches --config"
else
  notok "4 host.env CONFIG should reach --config, got: $argv"
fi

# --- 5: command-line env beats a `:=` host.env --------------------------------
printf ': "${DEPLOYMENT:=claw}"\n' > "$FIXSCRIPTS/host.env"
run_deploy DEPLOYMENT=other
argv="$(cat "$TESTROOT/argv")"
if [[ "$argv" == *"--name other"* ]]; then
  ok "5 command-line DEPLOYMENT beats a := host.env"
else
  notok "5 command-line env should beat a := host.env, got: $argv"
fi

# --- 6: a PLAIN assignment beats the command line — pinned, not preferred ----
printf 'DEPLOYMENT=claw\n' > "$FIXSCRIPTS/host.env"
run_deploy DEPLOYMENT=other
argv="$(cat "$TESTROOT/argv")"
if [[ "$argv" == *"--name claw"* ]]; then
  ok "6 plain assignment in host.env beats the command line (documented)"
else
  notok "6 plain assignment behavior changed — that needs a deliberate decision, got: $argv"
fi

# --- 7: GPU_IDS="" through host.env still disables the flag -------------------
printf 'GPU_IDS=""\n' > "$FIXSCRIPTS/host.env"
run_deploy HOST_ENV_SET=1
argv="$(cat "$TESTROOT/argv")"
if [ -n "$argv" ] && [[ "$argv" != *"--gpu-ids"* ]]; then
  ok "7 host.env GPU_IDS=\"\": no --gpu-ids"
else
  notok "7 host.env GPU_IDS=\"\" should pass no --gpu-ids, got: $argv"
fi

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]

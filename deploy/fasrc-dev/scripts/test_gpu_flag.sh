#!/usr/bin/env bash
# Self-test for the GPU flag contract in lib.sh's archi_deploy — exercised
# against a fake `archi` binary, so it renders no compose and starts no container.
#    1. by default NO --gpu-ids is passed
#    2. GPU_IDS=<n> still passes it through
#    3. GPU_IDS="" passes nothing (the documented disable)
#
# Why 1 matters: both containers are deliberately configured `device: cpu` and
# the models are served by a remote vLLM endpoint — on the GPU host the GPUs
# are owned by the vLLM servers, and the no-GPU host has no nvidia runtime at
# all. On such a host a default of GPU_IDS=0 renders `driver: nvidia, count:
# all` into the compose file, and the deploy dies with "could not select device
# driver nvidia" *after* recreating chatbot — taking the deployment down rather
# than failing before it touches it. OFF is the safe default on both hosts.
#
# Run: bash deploy/fasrc-dev/scripts/test_gpu_flag.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PASS=0; FAIL=0
ok()    { printf 'ok - %s\n' "$1"; PASS=$((PASS + 1)); }
notok() { printf 'not ok - %s\n' "$1"; FAIL=$((FAIL + 1)); }

TESTROOT="$(mktemp -d)"
trap 'rm -rf "$TESTROOT"' EXIT

# Fake `archi` that records the argv it was called with and exits clean.
mkdir -p "$TESTROOT/bin"
cat > "$TESTROOT/bin/archi" <<EOF
#!/usr/bin/env bash
printf '%s\n' "\$*" > "$TESTROOT/argv"
exit 0
EOF
chmod +x "$TESTROOT/bin/archi"

# Invoke archi_deploy in a subshell with the fake archi on PATH and the
# preflight/provisioning steps stubbed out — this test is about the flag only.
run_deploy() { # env assignments passed as "VAR=value" args
  : > "$TESTROOT/argv"
  # -u strips an ambient GPU_IDS so case 1 tests the default, not the shell.
  env -u GPU_IDS "$@" PATH="$TESTROOT/bin:$PATH" bash -c '
    source "'"$SCRIPT_DIR"'/lib.sh"
    require_files() { :; }
    ensure_config() { :; }
    check_llm()     { :; }
    archi_deploy >/dev/null 2>&1
  '
}

# --- 1: default passes no --gpu-ids ------------------------------------------
run_deploy GPU_IDS_UNSET=1
argv="$(cat "$TESTROOT/argv")"
if [ -n "$argv" ] && [[ "$argv" != *"--gpu-ids"* ]]; then
  ok "1 default: no --gpu-ids (host has no nvidia runtime and runs no models)"
else
  notok "1 default should pass no --gpu-ids, got: $argv"
fi

# --- 2: an explicit id is still honoured -------------------------------------
run_deploy GPU_IDS=1
argv="$(cat "$TESTROOT/argv")"
if [[ "$argv" == *"--gpu-ids 1"* ]]; then
  ok "2 explicit GPU_IDS=1: passed through"
else
  notok "2 explicit GPU_IDS=1 should be passed through, got: $argv"
fi

# --- 3: the documented empty-string disable still works ----------------------
run_deploy GPU_IDS=
argv="$(cat "$TESTROOT/argv")"
if [ -n "$argv" ] && [[ "$argv" != *"--gpu-ids"* ]]; then
  ok "3 GPU_IDS=\"\": no --gpu-ids"
else
  notok "3 GPU_IDS=\"\" should pass no --gpu-ids, got: $argv"
fi

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]

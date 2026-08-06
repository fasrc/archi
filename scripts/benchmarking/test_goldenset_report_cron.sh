#!/usr/bin/env bash
# Self-test for goldenset_report_cron.sh: exercises the wrapper's contract
# against a fake python — no network, no database, no crontab, nothing written
# outside a sandbox.
#    1. it invokes `goldenset_maintenance.py report` with the configured inputs
#    2. findings (exit 0) stay exit 0 — the cron must not alert on work-to-do
#    3. an operational failure (exit 1) propagates, so cron mails the operator
#    4. each run writes its own dated log file; earlier runs' files are untouched
#    5. the log directory is created if absent
#    6. a missing required setting fails before running anything
#    7. no model is passed unless GOLDENSET_MODEL is set
#    8. --min-pages is passed through when set, omitted when not
#    9-10. unattended: silent when healthy, stderr when broken (cron mails on
#          ANY output and ignores the exit status)
#   11-12. configuration comes from an env file, found by convention under HOME
#          (crontab has no line continuation, so the entry must carry no env)
#   13-15. the third state: findings exit ZERO, so notification cannot key on
#          the exit status — a concise digest, not the whole report
#   16-17. `goldenset-report-latest.log` tracks the newest run by relative name,
#          earlier files survive, and nothing is ever rotated to a `.1` suffix
#      24. with GOLDENSET_BANK unset the bank defaults to the provisioned config
#          checkout, not the examples/ path the bank used to occupy
#      25. a run that refuses up front writes no log and leaves `latest` alone
# Run: bash scripts/benchmarking/test_goldenset_report_cron.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CRON="$SCRIPT_DIR/goldenset_report_cron.sh"

# Hermetic against the developer's own cron config. Every knob below is one the
# wrapper honours, and it gives an ambient export precedence over the fake
# `python` on PATH and over these tests' defaults — so a shell that exported,
# say, GOLDENSET_PYTHON=/bin/false or a real GOLDENSET_ENV_FILE would make the
# gate source a real env file, run the real maintenance command, or just fail,
# none of which is this suite's contract. Clear them once so the sandbox is the
# only source of configuration; the env-file cases set GOLDENSET_ENV_FILE fresh.
unset GOLDENSET_ENV_FILE GOLDENSET_BANK GOLDENSET_PG_DSN GOLDENSET_CORPUS_JSON \
  GOLDENSET_SOURCES GOLDENSET_ALLOWED_HOSTS GOLDENSET_MIN_PAGES GOLDENSET_MAX_PAGES \
  GOLDENSET_LEDGER GOLDENSET_MODEL GOLDENSET_LOG_DIR GOLDENSET_PYTHON \
  GOLDENSET_LOG_MAX_BYTES

PASS=0; FAIL=0
ok()    { printf 'ok - %s\n' "$1"; PASS=$((PASS + 1)); }
notok() { printf 'not ok - %s\n' "$1"; FAIL=$((FAIL + 1)); }

TESTROOT="$(mktemp -d)"
trap 'rm -rf "$TESTROOT"' EXIT

# A fake `python` that records its argv and exits with $STUB_EXIT.
make_stub() { # $1 = sandbox
  local sb="$1"
  mkdir -p "$sb/bin"
  cat > "$sb/bin/python" <<EOF
#!/usr/bin/env bash
printf '%s\n' "\$*" >> "$sb/calls"
# Emulate \`report --summary-json\`: write whatever the test asked for.
prev=""
for a in "\$@"; do
  if [ "\$prev" = "--summary-json" ]; then
    printf '%s' "\${STUB_SUMMARY:-{\"gaps\":0,\"orphans\":0,\"drifted\":0,\"unchecked_sources\":0,\"failed_passes\":[],\"notify\":false}}" > "\$a"
  fi
  prev="\$a"
done
echo "fake report output"
if [ -n "\${STUB_BYTES:-}" ]; then head -c "\$STUB_BYTES" /dev/zero | tr '\\0' 'y'; echo; fi
exit \${STUB_EXIT:-0}
EOF
  chmod +x "$sb/bin/python"
}

# Run the wrapper in a sandbox with the fake python ahead of the real one.
# HOME is pinned to the sandbox so the wrapper's default env-file lookup
# (${GOLDENSET_ENV_FILE:-$HOME/.ralph/goldenset-report.env}) can never resolve to
# the developer's *real* installed cron config — otherwise, on a machine that has
# actually installed the nightly report, the gate would source that file, run the
# real maintenance command against the live corpus, and fail. Cases that exercise
# the HOME-convention lookup plant their env file under this sandbox HOME.
run_cron() { # $1 = sandbox; remaining env comes from the caller
  local sb="$1"; shift
  PATH="$sb/bin:$PATH" \
  HOME="$sb" \
  GOLDENSET_BANK="${GOLDENSET_BANK-$sb/bank.json}" \
  GOLDENSET_SOURCES="${GOLDENSET_SOURCES-$sb/sources.list}" \
  GOLDENSET_ALLOWED_HOSTS="${GOLDENSET_ALLOWED_HOSTS-docs.rc.fas.harvard.edu}" \
  GOLDENSET_LOG_DIR="${GOLDENSET_LOG_DIR-$sb/log}" \
    bash "$CRON" "$@"
}

new_sandbox() {
  local sb; sb="$(mktemp -d "$TESTROOT/sb.XXXXXX")"
  make_stub "$sb"
  : > "$sb/bank.json"
  : > "$sb/sources.list"
  echo "$sb"
}

# Each run names its log for its own UTC start time, so no test can hardcode the
# filename. Resolve it by glob and insist on exactly one match: a case holding two
# run logs has almost certainly invoked the wrapper twice, and a silent
# "newest wins" would hide that rather than fail on it.
run_log() { # $1 = log dir
  local matches=( "$1"/goldenset-report-*Z.log )
  if [ "${#matches[@]}" -ne 1 ] || [ ! -f "${matches[0]}" ]; then
    echo "expected exactly 1 run log in $1, found: ${matches[*]}" >&2
    return 1
  fi
  printf '%s\n' "${matches[0]}"
}

# 1. the report subcommand is invoked with the configured inputs
sb="$(new_sandbox)"
( GOLDENSET_PG_DSN="postgresql://x" run_cron "$sb" ) >/dev/null 2>&1 || true
calls="$(cat "$sb/calls" 2>/dev/null || true)"
case "$calls" in
  *"report"*) ok "invokes the report subcommand" ;;
  *) notok "invokes the report subcommand (got: $calls)" ;;
esac
case "$calls" in
  *"--pg-dsn postgresql://x"*) ok "passes the configured corpus DSN" ;;
  *) notok "passes the configured corpus DSN (got: $calls)" ;;
esac
case "$calls" in
  *"--allowed-hosts docs.rc.fas.harvard.edu"*) ok "passes the allowlist" ;;
  *) notok "passes the allowlist (got: $calls)" ;;
esac

# 2. findings must not alert: report exits 0, so the wrapper does too
sb="$(new_sandbox)"
if ( STUB_EXIT=0 GOLDENSET_PG_DSN="postgresql://x" run_cron "$sb" ) >/dev/null 2>&1; then
  ok "a run with findings exits zero"
else
  notok "a run with findings exits zero"
fi

# 3. an operational failure propagates, so cron mails the operator
sb="$(new_sandbox)"
if ( STUB_EXIT=1 GOLDENSET_PG_DSN="postgresql://x" run_cron "$sb" ) >/dev/null 2>&1; then
  notok "an operational failure exits non-zero"
else
  ok "an operational failure exits non-zero"
fi

# 4. each run writes its OWN dated file, and an earlier run's file is not touched.
#    One file per run is what makes "what did last night say" a file you open
#    rather than a region you locate inside a growing log.
sb="$(new_sandbox)"
mkdir -p "$sb/log"
prior="$sb/log/goldenset-report-20260101T000000Z.log"
echo "PREVIOUS RUN" > "$prior"
( GOLDENSET_PG_DSN="postgresql://x" run_cron "$sb" ) >/dev/null 2>&1 || true
fresh=""
for f in "$sb"/log/goldenset-report-*Z.log; do
  [ "$f" = "$prior" ] && continue
  fresh="$f"
done
if [ -n "$fresh" ] && grep -q "fake report output" "$fresh" \
   && grep -q "===== exit" "$fresh"; then
  ok "each run writes its own dated log file"
else
  notok "each run writes its own dated log file (found: $fresh)"
fi
if [ "$(cat "$prior")" = "PREVIOUS RUN" ]; then
  ok "an earlier run's log is left byte-for-byte alone"
else
  notok "an earlier run's log is left byte-for-byte alone"
fi

# 5. a missing log directory is created rather than losing the run
sb="$(new_sandbox)"
( GOLDENSET_PG_DSN="postgresql://x" GOLDENSET_LOG_DIR="$sb/deep/log" run_cron "$sb" ) \
  >/dev/null 2>&1 || true
if run_log "$sb/deep/log" >/dev/null 2>&1; then
  ok "creates a missing log directory"
else
  notok "creates a missing log directory"
fi

# 6. a missing required setting fails loudly, before invoking anything
sb="$(new_sandbox)"
if ( run_cron "$sb" ) >/dev/null 2>&1; then
  notok "refuses to run without a corpus setting"
else
  if [ -s "$sb/calls" ]; then
    notok "refuses BEFORE invoking the tool"
  else
    ok "refuses to run without a corpus setting, before invoking the tool"
  fi
fi

# 7. no provider call unless a model is explicitly configured
sb="$(new_sandbox)"
( GOLDENSET_PG_DSN="postgresql://x" run_cron "$sb" ) >/dev/null 2>&1 || true
case "$(cat "$sb/calls")" in
  *"--model"*) notok "omits --model by default" ;;
  *) ok "omits --model by default" ;;
esac
sb="$(new_sandbox)"
( GOLDENSET_PG_DSN="postgresql://x" GOLDENSET_MODEL="anthropic/x" run_cron "$sb" ) \
  >/dev/null 2>&1 || true
case "$(cat "$sb/calls")" in
  *"--model anthropic/x"*) ok "passes --model when configured" ;;
  *) notok "passes --model when configured" ;;
esac

# 8. the sitemap floor is passed through only when set
sb="$(new_sandbox)"
( GOLDENSET_PG_DSN="postgresql://x" GOLDENSET_MIN_PAGES=150 run_cron "$sb" ) \
  >/dev/null 2>&1 || true
case "$(cat "$sb/calls")" in
  *"--min-pages 150"*) ok "passes --min-pages when configured" ;;
  *) notok "passes --min-pages when configured" ;;
esac
sb="$(new_sandbox)"
( GOLDENSET_PG_DSN="postgresql://x" run_cron "$sb" ) >/dev/null 2>&1 || true
case "$(cat "$sb/calls")" in
  *"--min-pages"*) notok "omits --min-pages when unset" ;;
  *) ok "omits --min-pages when unset" ;;
esac

# 9. cron mails on ANY output, not on exit status — so a healthy unattended run
#    has to be completely silent or the nightly job mails its own report every
#    night and the operator learns to filter it.
sb="$(new_sandbox)"
out="$( ( GOLDENSET_PG_DSN="postgresql://x" run_cron "$sb" ) 2>"$sb/err" || true )"
if [ -z "$out" ] && [ ! -s "$sb/err" ]; then
  ok "a healthy unattended run is silent"
else
  notok "a healthy unattended run is silent (stdout: $out; stderr: $(cat "$sb/err"))"
fi

# 10. a failure must still reach the operator, which is what cron mail is for
sb="$(new_sandbox)"
( STUB_EXIT=1 GOLDENSET_PG_DSN="postgresql://x" run_cron "$sb" ) \
  >/dev/null 2>"$sb/err" || true
if grep -q "fake report output" "$sb/err"; then
  ok "a failing unattended run reports on stderr"
else
  notok "a failing unattended run reports on stderr (got: $(cat "$sb/err"))"
fi

# 11. an env file supplies the configuration, so the crontab line stays short
#     (crontab has no line continuation — the entry ends at the newline)
sb="$(new_sandbox)"
cat > "$sb/report.env" <<EOF
GOLDENSET_PG_DSN=postgresql://from-file
GOLDENSET_MIN_PAGES=150
EOF
( GOLDENSET_ENV_FILE="$sb/report.env" run_cron "$sb" ) >/dev/null 2>&1 || true
case "$(cat "$sb/calls" 2>/dev/null || true)" in
  *"--pg-dsn postgresql://from-file"*"--min-pages 150"*)
    ok "reads configuration from an env file" ;;
  *) notok "reads configuration from an env file (got: $(cat "$sb/calls" 2>/dev/null))" ;;
esac

# 12. and it is found by convention, so the cron entry needs no environment at all
#     (run_cron pins HOME to the sandbox, so $HOME/.ralph is $sb/.ralph here)
sb="$(new_sandbox)"
mkdir -p "$sb/.ralph"
cat > "$sb/.ralph/goldenset-report.env" <<EOF
GOLDENSET_PG_DSN=postgresql://by-convention
EOF
( GOLDENSET_PG_DSN="" run_cron "$sb" ) >/dev/null 2>&1 || true
case "$(cat "$sb/calls" 2>/dev/null || true)" in
  *"--pg-dsn postgresql://by-convention"*) ok "finds the default env file under HOME" ;;
  *) notok "finds the default env file under HOME (got: $(cat "$sb/calls" 2>/dev/null))" ;;
esac

# 13. a genuinely CLEAN unattended run stays silent
sb="$(new_sandbox)"
out="$( ( GOLDENSET_PG_DSN="postgresql://x" run_cron "$sb" ) 2>"$sb/err" || true )"
if [ -z "$out" ] && [ ! -s "$sb/err" ]; then
  ok "a clean unattended run is silent"
else
  notok "a clean unattended run is silent (stdout: $out; stderr: $(cat "$sb/err"))"
fi

# 14. findings exit ZERO by design, so keying notification on the exit status
#     would hide every actionable result. The wrapper reads the summary instead.
sb="$(new_sandbox)"
FINDINGS='{"gaps":2,"orphans":1,"drifted":3,"unchecked_sources":0,"failed_passes":[],"notify":true}'
out="$( ( STUB_SUMMARY="$FINDINGS" GOLDENSET_PG_DSN="postgresql://x" run_cron "$sb" ) \
  2>"$sb/err" || true )"
case "$out" in
  *"2 gap"*"1 orphan"*"3 drifted"*) ok "findings notify even though the run exits zero" ;;
  *) notok "findings notify even though the run exits zero (got: $out)" ;;
esac

# 15. ...but concisely. Mailing the whole report nightly is the alert fatigue
#     that made routine output log-only in the first place.
case "$out" in
  *"fake report output"*) notok "the findings notice is a digest, not the report" ;;
  *) ok "the findings notice is a digest, not the report" ;;
esac
# The digest must name THIS run's file. A shared path would make the operator
# hunt for the right banner, which is the ergonomics the per-run rename fixed.
# Guard on a resolved path first: an unresolvable one substitutes as the empty
# string, and every `case` pattern matches that — a vacuous pass.
digest_log="$(run_log "$sb/log" 2>/dev/null || true)"
if [ -n "$digest_log" ] && printf '%s' "$out" | grep -qF -- "$digest_log"; then
  ok "the findings notice points at this run's log"
else
  notok "the findings notice points at this run's log (got: $out)"
fi

# 16. `latest` is the whole point of the rename: reading the most recent report
#     must need no glob, no listing, and no timestamp arithmetic. The target is
#     relative so the log directory can be moved or copied without dangling.
sb="$(new_sandbox)"
( GOLDENSET_PG_DSN="postgresql://x" run_cron "$sb" ) >/dev/null 2>&1 || true
link="$sb/log/goldenset-report-latest.log"
if [ -L "$link" ] && [ "$(readlink "$link")" = "$(basename "$(run_log "$sb/log")")" ]; then
  ok "the latest symlink points at this run's file, by relative name"
else
  notok "the latest symlink points at this run's file, by relative name (got: $(readlink "$link" 2>&1))"
fi
if [ -r "$link" ] && grep -q "fake report output" "$link"; then
  ok "reading through the latest symlink yields the run's report"
else
  notok "reading through the latest symlink yields the run's report"
fi

# 17. a second run repoints `latest` without destroying the first run's file, and
#     no rotation happens at any size: there is no shared log left to rotate, so a
#     stray `.log.1` would mean the old append path survived the rename.
sb="$(new_sandbox)"
mkdir -p "$sb/log"
head -c 4096 /dev/zero | tr '\0' 'x' > "$sb/log/goldenset-report-20260101T000000Z.log"
ln -sfn "goldenset-report-20260101T000000Z.log" "$sb/log/goldenset-report-latest.log"
# Also seed the legacy shared name, over the cap. The old append path would
# rotate exactly this file, so its survival is what proves the path is gone.
head -c 4096 /dev/zero | tr '\0' 'x' > "$sb/log/goldenset-report.log"
( GOLDENSET_PG_DSN="postgresql://x" GOLDENSET_LOG_MAX_BYTES=1024 run_cron "$sb" ) \
  >/dev/null 2>&1 || true
if [ "$(readlink "$sb/log/goldenset-report-latest.log")" != "goldenset-report-20260101T000000Z.log" ] \
   && [ "$(wc -c < "$sb/log/goldenset-report-20260101T000000Z.log")" -eq 4096 ]; then
  ok "a later run repoints latest and keeps the earlier file"
else
  notok "a later run repoints latest and keeps the earlier file"
fi
if [ -z "$(find "$sb/log" -name '*.log.1' -print -quit)" ]; then
  ok "no log is ever rotated to a .1 suffix"
else
  notok "no log is ever rotated to a .1 suffix"
fi

# 18. drift "succeeds" as long as ONE source was readable, so a run that checked
#     1 of 50 pages exits zero with drifted=0. Notification keys on the report's
#     own `notify` flag, not on the drifted count, or that degraded run is
#     indistinguishable from a clean bank.
sb="$(new_sandbox)"
DEGRADED='{"gaps":0,"orphans":0,"drifted":0,"unchecked_sources":49,"failed_passes":[],"notify":true}'
out="$( ( STUB_SUMMARY="$DEGRADED" GOLDENSET_PG_DSN="postgresql://x" run_cron "$sb" ) \
  2>/dev/null || true )"
case "$out" in
  *"49 unchecked"*) ok "a degraded run notifies even with nothing drifted" ;;
  *) notok "a degraded run notifies even with nothing drifted (got: $out)" ;;
esac

# 18b. `notify` can fire on a slug near-miss alone (needs_reconciliation), a
#      bucket the digest first omitted — so the mail paged with an all-zeros
#      line and no named cause, forcing the operator to open the log to learn
#      why it spoke. The digest must name the reconciliation count.
sb="$(new_sandbox)"
RECON='{"gaps":0,"orphans":0,"drifted":0,"unchecked_sources":0,"needs_reconciliation":2,"orphans_needs_reconciliation":1,"refused_sources":0,"failed_passes":[],"notify":true}'
out="$( ( STUB_SUMMARY="$RECON" GOLDENSET_PG_DSN="postgresql://x" run_cron "$sb" ) \
  2>/dev/null || true )"
case "$out" in
  *"3 reconcile"*) ok "a reconciliation-only notify names the reconcile bucket" ;;
  *) notok "a reconciliation-only notify names the reconcile bucket (got: $out)" ;;
esac

# 19. the flag is authoritative — the wrapper does not re-derive the policy from
#     the counters. A deliberately contradictory summary (counts set, notify
#     false) pins that: which buckets deserve to wake someone is decided in
#     `report`, where it has tests, not in shell string-matching.
sb="$(new_sandbox)"
QUIET='{"gaps":0,"orphans":0,"drifted":0,"unchecked_sources":0,"refused_sources":7,"failed_passes":[],"notify":false}'
out="$( ( STUB_SUMMARY="$QUIET" GOLDENSET_PG_DSN="postgresql://x" run_cron "$sb" ) \
  2>/dev/null || true )"
if [ -z "$out" ]; then
  ok "notify=false stays silent even with non-zero counters"
else
  notok "notify=false stays silent even with non-zero counters (got: $out)"
fi

# 20. the env file is SOURCED, so a multi-value setting must be quoted in it.
#     Unquoted, bash reads `VAR=a b` as "run command b with VAR=a" — which is
#     how the allowlist, the one setting that is normally a list, breaks.
sb="$(new_sandbox)"
cat > "$sb/report.env" <<'EOF'
GOLDENSET_PG_DSN=postgresql://x
GOLDENSET_ALLOWED_HOSTS="docs.rc.fas.harvard.edu slurm.schedmd.com"
EOF
( GOLDENSET_ENV_FILE="$sb/report.env" run_cron "$sb" ) >/dev/null 2>&1 || true
case "$(cat "$sb/calls" 2>/dev/null || true)" in
  *"--allowed-hosts docs.rc.fas.harvard.edu slurm.schedmd.com"*)
    ok "a quoted multi-host allowlist survives the env file" ;;
  *) notok "a quoted multi-host allowlist survives the env file (got: $(cat "$sb/calls" 2>/dev/null))" ;;
esac

# 21. one file per run bounds the file COUNT at one per night, not the SIZE of any
#     one of them: coverage prints every gap and drift can span the whole bank, so
#     a single run can be enormous. The cap is what keeps each file readable.
sb="$(new_sandbox)"
( STUB_BYTES=20000 GOLDENSET_PG_DSN="postgresql://x" GOLDENSET_LOG_MAX_BYTES=2048 \
  run_cron "$sb" ) >/dev/null 2>&1 || true
size="$(wc -c < "$(run_log "$sb/log")")"
if [ "$size" -le 4096 ]; then
  ok "a single oversized run cannot blow past the cap (log is $size bytes)"
else
  notok "a single oversized run cannot blow past the cap (log is $size bytes)"
fi

# 22. and the truncation is announced, so nobody reads a cut log as a whole one
sb="$(new_sandbox)"
( STUB_BYTES=20000 GOLDENSET_PG_DSN="postgresql://x" GOLDENSET_LOG_MAX_BYTES=2048 \
  run_cron "$sb" ) >/dev/null 2>&1 || true
if grep -q "truncated" "$(run_log "$sb/log")"; then
  ok "a truncated log says so"
else
  notok "a truncated log says so"
fi

# 23. a normal-sized run is not truncated
sb="$(new_sandbox)"
( GOLDENSET_PG_DSN="postgresql://x" GOLDENSET_LOG_MAX_BYTES=1048576 run_cron "$sb" ) \
  >/dev/null 2>&1 || true
whole="$(run_log "$sb/log")"
if grep -q "fake report output" "$whole" && ! grep -q "truncated" "$whole"; then
  ok "a normal run is written whole"
else
  notok "a normal run is written whole"
fi

# 24. the bank no longer ships in this repo — it lives in archi-config, provisioned
#     at config/ on deploy. With GOLDENSET_BANK unset the wrapper must default
#     there, not at the examples/ path the bank used to occupy. Asserted as a
#     path string, not a file that exists: config/ is gitignored, so a fresh
#     clone and CI both run this test without the checkout present.
sb="$(new_sandbox)"
( GOLDENSET_BANK= GOLDENSET_PG_DSN="postgresql://x" run_cron "$sb" ) >/dev/null 2>&1 || true
calls="$(cat "$sb/calls" 2>/dev/null || true)"
repo_root="$(cd "$SCRIPT_DIR/../.." && pwd)"
case "$calls" in
  *"--bank $repo_root/config/benchmarking/fasrc_ragas_queries.json"*)
    ok "defaults the bank to the provisioned config checkout" ;;
  *) notok "defaults the bank to the provisioned config checkout (got: $calls)" ;;
esac
# 25. a wrapper that refuses before invoking anything must not disturb the pointer:
#     `latest` resolving to a run that never happened is worse than no pointer,
#     because a stale report reads exactly like a current one.
sb="$(new_sandbox)"
mkdir -p "$sb/log"
echo "PREVIOUS RUN" > "$sb/log/goldenset-report-20260101T000000Z.log"
ln -sfn "goldenset-report-20260101T000000Z.log" "$sb/log/goldenset-report-latest.log"
( GOLDENSET_PG_DSN="postgresql://x" GOLDENSET_SOURCES="" run_cron "$sb" ) \
  >/dev/null 2>&1 || true
if [ "$(readlink "$sb/log/goldenset-report-latest.log")" = "goldenset-report-20260101T000000Z.log" ] \
   && [ "$(find "$sb/log" -name 'goldenset-report-*Z.log' | wc -l)" -eq 1 ]; then
  ok "a misconfigured run writes no log and leaves latest alone"
else
  notok "a misconfigured run writes no log and leaves latest alone"
fi

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]

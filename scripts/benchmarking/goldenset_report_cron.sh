#!/usr/bin/env bash
# Nightly read-only maintenance report over the RAGAS golden-set bank.
#
# Runs `goldenset_maintenance.py report` — coverage + orphans + drift — and
# appends the output to a log. Writes nothing else: not the bank, not the
# corpus, not the decision ledger.
#
# Exit status follows the cron contract, so what lands in the operator's mail is
# only ever a broken run:
#   0  the report ran, whatever it found (gaps and drift are work, not failure)
#   1  a pass could not run — unreadable corpus/bank/source list, or a live
#      inventory too incomplete to judge orphans against
#   2  this wrapper is misconfigured (checked before anything is invoked)
#
# Configure with an environment file, not flags, because crontab has no line
# continuation — an entry ends at the newline, and a trailing backslash does not
# join the next line. So the settings live in a file a human can read and the
# crontab entry stays a single short line with no environment on it at all:
#
#   GOLDENSET_ENV_FILE      env file to source (default: ~/.ralph/goldenset-report.env)
#
# Values in that file win over anything already in the environment, the way
# systemd's EnvironmentFile= behaves. Recognized settings:
#
#   GOLDENSET_BANK          bank JSON       (default: examples/benchmarking/…)
#   GOLDENSET_PG_DSN        live catalog DSN     ) exactly one
#   GOLDENSET_CORPUS_JSON   or a corpus dump     ) of these two
#   GOLDENSET_SOURCES       source list the KB ingests from   (required)
#   GOLDENSET_ALLOWED_HOSTS space-separated hosts to contact  (required)
#   GOLDENSET_MIN_PAGES     sitemap floor — match the deployment (FASRC: 150)
#   GOLDENSET_MAX_PAGES     sitemap cap  — match the deployment
#   GOLDENSET_LEDGER        decision ledger, so declined pages stay suppressed
#   GOLDENSET_MODEL         OPTIONAL provider/model for the advisory drift diff.
#                           Unset by default: an unattended job should not spend
#                           a provider call per drifted row without being asked.
#   GOLDENSET_LOG_DIR       where to append   (default: ~/.ralph/log)
#   GOLDENSET_PYTHON        interpreter       (default: python)
#
# Install and rollback are documented in docs/docs/benchmarking.md.
# Self-test: bash scripts/benchmarking/test_goldenset_report_cron.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Sourced before anything is read, so the crontab entry can carry no environment.
ENV_FILE="${GOLDENSET_ENV_FILE:-$HOME/.ralph/goldenset-report.env}"
if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

BANK="${GOLDENSET_BANK:-$REPO_ROOT/examples/benchmarking/fasrc_ragas_queries.json}"
LOG_DIR="${GOLDENSET_LOG_DIR:-$HOME/.ralph/log}"
LOG="$LOG_DIR/goldenset-report.log"
PYTHON="${GOLDENSET_PYTHON:-python}"

die() { printf 'goldenset-report: %s\n' "$1" >&2; exit 2; }

# Validate BEFORE invoking anything. A misconfigured cron that half-runs is
# worse than one that refuses: the report would be silently partial, and a
# partial report reads exactly like a clean one.
[ -n "${GOLDENSET_SOURCES:-}" ] || die "GOLDENSET_SOURCES is required"
[ -n "${GOLDENSET_ALLOWED_HOSTS:-}" ] || die "GOLDENSET_ALLOWED_HOSTS is required"
if [ -n "${GOLDENSET_PG_DSN:-}" ] && [ -n "${GOLDENSET_CORPUS_JSON:-}" ]; then
  die "set GOLDENSET_PG_DSN or GOLDENSET_CORPUS_JSON, not both"
fi

args=("$REPO_ROOT/scripts/benchmarking/goldenset_maintenance.py" report
      --bank "$BANK" --sources "$GOLDENSET_SOURCES")

if [ -n "${GOLDENSET_PG_DSN:-}" ]; then
  args+=(--pg-dsn "$GOLDENSET_PG_DSN")
elif [ -n "${GOLDENSET_CORPUS_JSON:-}" ]; then
  args+=(--corpus-json "$GOLDENSET_CORPUS_JSON")
else
  die "GOLDENSET_PG_DSN or GOLDENSET_CORPUS_JSON is required"
fi

# Deliberately unquoted: the allowlist is a space-separated list and argparse
# takes it as nargs="+".
# shellcheck disable=SC2206
args+=(--allowed-hosts ${GOLDENSET_ALLOWED_HOSTS})

[ -n "${GOLDENSET_MIN_PAGES:-}" ] && args+=(--min-pages "$GOLDENSET_MIN_PAGES")
[ -n "${GOLDENSET_MAX_PAGES:-}" ] && args+=(--max-pages "$GOLDENSET_MAX_PAGES")
[ -n "${GOLDENSET_LEDGER:-}" ] && args+=(--ledger "$GOLDENSET_LEDGER")
[ -n "${GOLDENSET_MODEL:-}" ] && args+=(--model "$GOLDENSET_MODEL")

SUMMARY="$(mktemp)"
trap 'rm -f "$SUMMARY" "${RUN_OUT:-}"' EXIT
args+=(--summary-json "$SUMMARY")

mkdir -p "$LOG_DIR"

# A nightly append with no ceiling eventually fills the filesystem, and the
# first thing to break is the logging itself — so the failure that finally
# needs reading is the one that cannot be written. One rotation is enough: the
# log is a history to skim, not an archive to audit, and a hard 2x bound needs
# no logrotate unit, which keeps rollback "delete the cron line".
LOG_MAX_BYTES="${GOLDENSET_LOG_MAX_BYTES:-5242880}"
if [ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -ge "$LOG_MAX_BYTES" ]; then
  mv -f "$LOG" "$LOG.1"
fi

if ! printf '\n===== goldenset report %s =====\n' \
     "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" >> "$LOG"; then
  die "cannot write $LOG"
fi

# Appended, never truncated: the value of this log is the history, which is how
# a slow drift (a page edited a little each month) becomes visible at all.
set +e
if [ -t 1 ]; then
  # Interactive: stream, so a slow drift pass is visible while it runs.
  "$PYTHON" "${args[@]}" 2>&1 | tee -a "$LOG"
  status=${PIPESTATUS[0]}
else
  RUN_OUT="$(mktemp)"
  "$PYTHON" "${args[@]}" > "$RUN_OUT" 2>&1
  status=$?
  cat "$RUN_OUT" >> "$LOG"
fi
set -e

printf '===== exit %d =====\n' "$status" >> "$LOG"

# Three states, and cron only gives us two signals — it mails on ANY output and
# ignores the exit status entirely. So the wrapper has to choose what to say:
#
#   broken   -> the whole report on stderr. Someone must look now.
#   findings -> a one-line digest. `report` exits ZERO on findings by design
#               (the cron contract), so keying on the exit status would bury
#               every actionable result in a log nobody tails — the job would
#               detect stale benchmark data indefinitely and tell no one.
#   clean    -> nothing. Mailing a wall of text nightly is how an operator
#               learns to filter the mail, which costs the broken case above.
if [ ! -t 1 ]; then
  if [ "$status" -ne 0 ]; then
    cat "${RUN_OUT:-/dev/null}" >&2
  else
    count_of() { tr -d ' \n' < "$SUMMARY" | grep -o "\"$1\":[0-9]*" | head -1 |
                 cut -d: -f2; }
    gaps="$(count_of gaps)";       gaps="${gaps:-0}"
    orphans="$(count_of orphans)"; orphans="${orphans:-0}"
    drifted="$(count_of drifted)"; drifted="${drifted:-0}"
    recon="$(count_of needs_reconciliation)"; recon="${recon:-0}"
    if [ $((gaps + orphans + drifted + recon)) -gt 0 ]; then
      printf 'goldenset report: %s gaps | %s orphans | %s drifted | %s need reconciliation\n' \
        "$gaps" "$orphans" "$drifted" "$recon"
      printf 'full report: %s\n' "$LOG"
    fi
  fi
fi

exit "$status"

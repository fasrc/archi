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
# systemd's EnvironmentFile= behaves. It is *sourced*, so quote any value with a
# space in it — unquoted, `VAR=a b` is shell for "run b with VAR=a", which bites
# the allowlist because it is the one setting that is normally a list.
# Recognized settings:
#
#   GOLDENSET_BANK          bank JSON         (default: config/benchmarking/…)
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
#   GOLDENSET_LOG_DIR       where each run's dated log lands, alongside a
#                           `goldenset-report-latest.log` symlink to the newest
#                           (default: ~/.ralph/log)
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

BANK="${GOLDENSET_BANK:-$REPO_ROOT/config/benchmarking/fasrc_ragas_queries.json}"
LOG_DIR="${GOLDENSET_LOG_DIR:-$HOME/.ralph/log}"
PYTHON="${GOLDENSET_PYTHON:-python}"

# One file per run, named for the run's UTC start. Colons are legal in a filename
# but make globs, completion and scp quoting tedious, so the compact form.
# Lexicographic order is chronological order, which is what makes `ls` useful.
RUN_STAMP="$(date -u '+%Y%m%dT%H%M%SZ')"
# The in-file banner is derived from that same string rather than a second `date`
# call: two calls can straddle a second boundary, and a file whose name disagrees
# with its own header is a thing nobody should have to reason about.
RUN_ISO="${RUN_STAMP:0:4}-${RUN_STAMP:4:2}-${RUN_STAMP:6:2}"
RUN_ISO="${RUN_ISO}T${RUN_STAMP:9:2}:${RUN_STAMP:11:2}:${RUN_STAMP:13:2}Z"
LOG_NAME="goldenset-report-$RUN_STAMP.log"
LOG="$LOG_DIR/$LOG_NAME"
# A stable path for the overwhelmingly common question — "what did the last run
# say?" — so answering it needs no glob and no timestamp arithmetic.
LATEST="$LOG_DIR/goldenset-report-latest.log"

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

# Bounds ONE run's logged output, not the directory. Coverage prints every gap and
# drift can span the whole bank, so a single run can be enormous; a file nobody can
# open is a file nobody reads. The number of files is deliberately unbounded — a
# nightly run is tens of KB, and an automatic pruner would trade immaterial disk
# for a way to lose history to a config typo. Operators prune by hand.
LOG_MAX_BYTES="${GOLDENSET_LOG_MAX_BYTES:-5242880}"

if ! printf '===== goldenset report %s =====\n' "$RUN_ISO" >> "$LOG"; then
  die "cannot write $LOG"
fi

# Repointed only after the run's own file exists, so `latest` can never name a
# file that could not be created. `-n` treats an existing symlink as a file to
# replace rather than a directory to write inside, which is what turns the second
# night into a repoint instead of a link nested under the first night's target.
ln -sfn "$LOG_NAME" "$LATEST" 2>/dev/null ||
  printf 'goldenset-report: could not update %s\n' "$LATEST" >&2

# Appended within this run's own file, never truncating it: the history that makes
# a slow drift visible (a page edited a little each month) now lives across the
# dated files rather than inside one, so the directory is the record to skim.
set +e
if [ -t 1 ]; then
  # Interactive: stream, so a slow drift pass is visible while it runs.
  "$PYTHON" "${args[@]}" 2>&1 | tee -a "$LOG"
  status=${PIPESTATUS[0]}
else
  RUN_OUT="$(mktemp)"
  "$PYTHON" "${args[@]}" > "$RUN_OUT" 2>&1
  status=$?
  # One file per run bounds the file COUNT, not the size of any one of them:
  # coverage prints every gap and drift can span the whole bank, so ONE run can be
  # enormous. Cap what lands on disk; the full text still goes to stderr when the
  # run failed, so nothing diagnostic is lost at the moment it matters.
  if [ "$(wc -c < "$RUN_OUT")" -gt "$LOG_MAX_BYTES" ]; then
    head -c "$LOG_MAX_BYTES" "$RUN_OUT" >> "$LOG"
    printf '\n[... truncated at %s bytes; raise GOLDENSET_LOG_MAX_BYTES to keep more ...]\n' \
      "$LOG_MAX_BYTES" >> "$LOG"
  else
    cat "$RUN_OUT" >> "$LOG"
  fi
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
    read_num() { tr -d ' \n' < "$SUMMARY" | grep -o "\"$1\":[0-9]*" | head -1 |
                 cut -d: -f2; }
    # Whether to speak is decided by `report`, not re-derived here: which buckets
    # deserve to wake someone is a judgement about the domain, and it belongs
    # where it has tests rather than in shell string-matching.
    notify="$(tr -d ' \n' < "$SUMMARY" | grep -o '"notify":[a-z]*' | head -1 |
              cut -d: -f2)"
    if [ "$notify" = "true" ]; then
      # Both slug-near-miss buckets fold into one `reconcile` field. `notify` can
      # fire on reconciliation alone (both are in report's _NOTIFY_ON), and a
      # digest that omits them pages with an all-zeros line and no named cause —
      # the operator then has to open the log just to learn why it spoke.
      # `|| true`: read_num is a grep pipeline under `set -o pipefail`, so a
      # summary without these keys makes the assignment itself non-zero and
      # `set -e` would abort before the digest ever prints. The printf args below
      # tolerate a missing key already (a failed command substitution there does
      # not trip set -e); a bare assignment does not.
      nr="$(read_num needs_reconciliation || true)"
      onr="$(read_num orphans_needs_reconciliation || true)"
      printf 'goldenset report: %s gaps | %s orphans | %s drifted | %s reconcile | %s unchecked | %s refused\n' \
        "$(read_num gaps)" "$(read_num orphans)" "$(read_num drifted)" \
        "$(( ${nr:-0} + ${onr:-0} ))" \
        "$(read_num unchecked_sources)" "$(read_num refused_sources)"
      printf 'full report: %s\n' "$LOG"
    fi
  fi
fi

exit "$status"

#!/usr/bin/env python
"""Read-only maintenance passes over the RAGAS golden-set question bank.

Answers three questions an operator otherwise has to eyeball by hand:

- ``coverage`` — which ingested KB pages does no bank row ground against?
- ``orphans`` — which bank rows cite a page the live KB no longer publishes?
- ``drift`` — which confirmed rows were grounded in a page that has since changed?

Every pass is **proposal-only**: they print work lists and leave the bank file
byte-unchanged. Adding a question, locking a reference, re-baselining a drifted
row, or pruning an orphan is a separate, explicitly human-initiated step.
``--propose`` drafts candidates for one greenlit gap and prints them;
``--decline``/``--undecline`` record and reverse the operator's dismissal of a gap
in a decision ledger, which is the only file this tool writes.

Exit codes follow the cron contract: **0 even when there are findings** (gaps and
orphans are work to do, not a broken run), non-zero only on operational failure —
an unreadable bank, corpus, or source list.

Usage:
    # coverage against a JSON dump of the corpus (hermetic / offline)
    python scripts/benchmarking/goldenset_maintenance.py coverage \\
        --bank examples/benchmarking/fasrc_ragas_queries.json \\
        --corpus-json corpus.json [--source-type web] [--path-glob 'https://…/kb/*']

    # coverage straight from the live catalog
    python scripts/benchmarking/goldenset_maintenance.py coverage \\
        --bank <bank.json> --pg-dsn "postgresql://archi@localhost/archi-db"

    # orphans against the current source list (sitemap- lines are expanded live)
    python scripts/benchmarking/goldenset_maintenance.py orphans \\
        --bank <bank.json> --sources config/lists/sources.list --min-pages 150

    # draft candidates for one greenlit gap, grounded in the persisted document
    python scripts/benchmarking/goldenset_maintenance.py coverage \\
        --bank <bank.json> --pg-dsn <dsn> --propose <url> \\
        --model anthropic/claude-sonnet-5 --data-path <data-root>

    # dismiss a gap, and undo that
    python scripts/benchmarking/goldenset_maintenance.py coverage \\
        --bank <bank.json> --pg-dsn <dsn> --decline <url> --ledger <ledger.json>
    python scripts/benchmarking/goldenset_maintenance.py coverage \\
        --bank <bank.json> --undecline <url> --ledger <ledger.json>

    # fact drift: re-fetch every locked row's sources and report what moved
    python scripts/benchmarking/goldenset_maintenance.py drift \\
        --bank <bank.json> [--model anthropic/claude-sonnet-5] \\
        [--allowed-hosts docs.rc.fas.harvard.edu] [--show-text] [--print-hashes]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional, Sequence

try:
    import fcntl  # POSIX-only; the ledger lock degrades loudly without it.
except ImportError:  # pragma: no cover - non-POSIX
    fcntl = None  # type: ignore[assignment]

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.utils.benchmark_schema import normalize_bank  # noqa: E402  # isort: skip
from src.utils.goldenset_maintenance import (  # noqa: E402  # isort: skip
    DRIFT_INCOMPARABLE,
    DRIFT_REFUSED,
    DRIFT_UNBASELINED,
    DRIFT_UNREACHABLE,
    ProposalError,
    bank_source_urls,
    build_live_inventory,
    canonical_url,
    declined_urls,
    filter_docs,
    find_coverage_gaps,
    find_drift,
    find_orphans,
    group_by_parent,
    propose_candidates,
    read_corpus_docs,
    read_declines,
    reconcile,
    resolve_persisted_path,
    with_decline,
    without_decline,
)


#: Body cap for a drift re-fetch. A KB article is a few hundred KB; the ingest's
#: own 64 MiB ceiling exists for whole-site sitemap indexes and is far too much
#: headroom for a pass that fetches every source in the bank.
MAX_PAGE_BYTES = 8 * 1024 * 1024


class OperationalError(Exception):
    """A failure of the run itself (unreadable input), not a finding.

    Its implicit promise is "nothing was changed" — every raise site must be on
    the safe side of any commit point, so an operator can retry.
    """


class LedgerNotDurable(OperationalError):
    """The ledger WAS replaced, but the change could not be confirmed durable.

    Deliberately breaks the base class's "nothing happened" reading, which is
    why it is a distinct type: the mutation is committed and in effect. Telling
    the operator the write failed would have them redo a decision that already
    landed.
    """


def _load_json(path: str) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError) as exc:
        raise OperationalError(f"cannot read {path}: {exc}") from exc


def load_bank(path: str) -> List[Any]:
    """Load the bank through `benchmark_schema`, so the legacy dialect still works."""
    bank = normalize_bank(_load_json(path))
    if not isinstance(bank, list):
        raise OperationalError(f"{path} is not a bank array")
    return bank


def corpus_rows_from_json(path: str):
    """Row fetcher over a JSON dump of `documents` — for offline runs and tests."""
    rows = _load_json(path)
    if not isinstance(rows, list):
        raise OperationalError(f"{path} is not a list of corpus rows")
    return lambda: rows


# Coverage asks "which RETRIEVABLE pages have no question?", so the corpus read is
# restricted to documents the RAG pipeline can actually serve. `ingestion_status` is
# one of pending/embedding/embedded/failed and rows are inserted as `pending`; only
# `embedded` has chunks. Without the filter, coverage asks an operator to author a
# golden question for a page the agent cannot retrieve — an unanswerable question
# that would then score as a benchmark failure.
CORPUS_SQL = (
    "SELECT url, source_type, file_path FROM documents "
    "WHERE NOT is_deleted AND ingestion_status = 'embedded'"
)


def corpus_rows_from_postgres(dsn: str):
    """Row fetcher over the live catalog, mirroring the ingestion-verifier read."""

    def fetch():
        import psycopg2
        import psycopg2.extras

        try:
            with psycopg2.connect(dsn) as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(CORPUS_SQL)
                    return [dict(row) for row in cur.fetchall()]
        except Exception as exc:  # pragma: no cover - needs a live database
            raise OperationalError(f"cannot read the corpus: {exc}") from exc

    return fetch


@contextmanager
def ledger_lock(path: str):
    """Serialize the ledger read-modify-write across processes.

    Atomicity alone does not make the update safe: two writers can each read the
    same ledger, append a different URL, and replace the file — the second
    replacement silently erases the first decline, which then resurfaces as a
    coverage gap while both commands report success. The lock covers read, merge
    and replace as one transaction.

    The lock lives on a **sidecar** path, never on the ledger itself:
    `write_ledger` swaps the ledger's inode via `os.replace`, so a lock taken on
    the ledger file would be a lock on a file the next writer never opens.

    `fcntl` is POSIX-only, and where it is missing this **refuses** rather than
    proceeding with a warning. A warning is not a mitigation — the lost update
    happens either way, and the operator has no way to notice. Only ledger
    mutation takes this path; coverage, orphans and `--propose` are read-only and
    still run.
    """
    lock_path = Path(f"{path}.lock")
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OperationalError(f"cannot create ledger lock {lock_path}: {exc}") from exc

    if fcntl is None:
        raise OperationalError(
            f"refusing to modify {path}: an exclusive file lock is required and "
            "`fcntl` is unavailable on this platform. Without it a concurrent "
            "decline is silently lost, and a decline cannot be rebuilt from the "
            "bank. Read-only passes (coverage, orphans, --propose) still work."
        )

    try:
        handle = open(lock_path, "a+", encoding="utf-8")
    except OSError as exc:
        raise OperationalError(f"cannot open ledger lock {lock_path}: {exc}") from exc
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        handle.close()  # releases the flock


def read_ledger(path: Optional[str]) -> List[Any]:
    """Load and validate the decision ledger, or an empty one when absent.

    A *missing* ledger legitimately means "nothing declined yet". Anything
    malformed — bad JSON, not an array, or a single unusable entry — is an
    operational failure: reading past it would drop operator decisions on an
    otherwise green run, and a decline cannot be reconstructed from the bank.
    """
    if not path:
        return []
    if not Path(path).exists():
        return []
    entries = _load_json(path)
    try:
        read_declines(entries)
    except ValueError as exc:
        raise OperationalError(f"ledger {path}: {exc}") from exc
    return entries


def write_ledger(path: str, entries: List[Any]) -> None:
    """Atomically persist the ledger — the ONLY file this tool writes.

    Written to a same-directory temp file, flushed and fsynced, then
    `os.replace`d over the target, then the parent directory is fsynced. A plain
    `write_text` truncates first, so a crash or a full disk mid-write would leave
    a mangled ledger and lose every decline it held — the one record that cannot
    be re-derived from the bank. Syncing the file alone is not enough either:
    POSIX does not guarantee the *rename* is durable until the directory entry is
    synced, so a crash right after a successful-looking run could resurrect every
    dismissed page. The temp file is removed on any failure so a failed run
    leaves no litter.

    `os.replace` is the commit point, and the error a caller sees has to match
    which side of it failed:

    - **before** it — nothing was mutated, so this raises `OperationalError` and
      the operator can safely retry. The directory handle is opened up front for
      exactly this reason: a directory that cannot be synced at all then fails
      while the old ledger is still intact, rather than after the swap.
    - **after** it — the new ledger *is* the ledger. Only durability is in
      question, so this raises `LedgerNotDurable`. Reporting that as "cannot
      write" would tell the operator nothing happened and invite them to redo a
      change that already took effect.
    """
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OperationalError(f"cannot write ledger {path}: {exc}") from exc

    payload = json.dumps(entries, indent=2, ensure_ascii=False) + "\n"
    handle = None
    tmp_name = ""
    dir_fd = None
    try:
        dir_fd = _open_directory(target.parent)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp"
        )
        handle = os.fdopen(fd, "w", encoding="utf-8")
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        handle = None
        os.replace(tmp_name, target)
    except OSError as exc:
        if handle is not None:
            handle.close()
        if tmp_name:
            try:
                os.unlink(tmp_name)
            except OSError:  # pragma: no cover - already gone
                pass
        if dir_fd is not None:
            os.close(dir_fd)
        raise OperationalError(f"cannot write ledger {path}: {exc}") from exc

    # --- committed. Everything below is durability, never "did it happen". ---
    if dir_fd is None:  # pragma: no cover - non-POSIX
        return
    try:
        os.fsync(dir_fd)
    except OSError as exc:
        raise LedgerNotDurable(
            f"the ledger {path} WAS updated, but its directory entry could not be "
            f"synced ({exc}). The change is in effect now and may not survive a "
            "host crash. Do not retry it — check the storage instead."
        ) from exc
    finally:
        os.close(dir_fd)


def _open_directory(directory: Path) -> Optional[int]:
    """Open a directory for fsync, or None where that is not a thing.

    Non-POSIX platforms cannot open a directory for reading — the same boundary
    the ledger lock draws.
    """
    if not hasattr(os, "O_DIRECTORY"):  # pragma: no cover - non-POSIX
        return None
    return os.open(str(directory), os.O_RDONLY)


def read_persisted_document(doc, data_path: Optional[str]) -> str:
    """Read the persisted text for a corpus doc — what the retriever serves.

    Grounding must use the *indexed* body, not a live re-fetch. Ingestion is
    URL-keyed and skips the content write for a page it already holds
    (`persist_resource(..., overwrite=False)`), so the live page can be ahead of
    the index. A candidate grounded in live-only text would ask about a fact the
    retriever cannot serve — the very failure the retrievability filter exists to
    prevent, reintroduced one layer down. Every failure here refuses the run
    rather than falling back to a fetch.
    """
    if not data_path:
        raise OperationalError(
            "--propose needs --data-path <dir> — the deployment's data root, where "
            "`documents.file_path` resolves. Candidates are grounded in the "
            "persisted (indexed) text, never in a live re-fetch."
        )
    if not doc.file_path:
        raise OperationalError(
            f"{doc.url} has no file_path in the corpus — nothing to ground in. "
            "(A `--corpus-json` dump must include the column.)"
        )
    try:
        path = resolve_persisted_path(doc.file_path, data_path)
    except ValueError as exc:
        # Containment failure: the row points outside the data root. Refuse here,
        # before any read and long before the model call -- the contents would
        # otherwise leave the machine for an external provider.
        raise OperationalError(str(exc)) from exc
    if path is None:  # pragma: no cover - guarded by the file_path check above
        raise OperationalError(f"{doc.url} has no resolvable persisted document")
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise OperationalError(f"cannot read the persisted document {path}: {exc}")
    if not text.strip():
        raise OperationalError(f"the persisted document {path} is empty")
    return text


def build_ask_llm(model: str):
    """Build the `prompt -> reply text` callable from a `provider/model` string."""
    if "/" not in model:
        raise OperationalError(
            f"--model must be 'provider/model' (e.g. anthropic/claude-sonnet-5), got {model!r}"
        )
    provider, _, model_name = model.partition("/")

    def ask(prompt: str) -> str:
        from src.archi.providers import get_model

        try:
            chat = get_model(provider, model_name, {})
            reply = chat.invoke(prompt)
        except Exception as exc:  # pragma: no cover - needs a live provider
            raise OperationalError(f"cannot reach {model}: {exc}") from exc
        content = getattr(reply, "content", reply)
        return content if isinstance(content, str) else str(content)

    return ask


def build_fetch_html():
    """The page fetcher drift re-fetches sources with.

    Reuses the ingest's own fetch for its transport limits — no cross-host
    redirects, a bounded body, a timeout. Those are the ONLY protections it
    carries: the ingest's *target* policy (`is_url_allowed`) lives in
    `expand_sitemaps`, not here, so `find_drift` applies it itself before
    handing any URL to this callable.

    The body cap is tightened well below the ingest's 64 MiB. That ceiling is
    sized for a sitemap index of a whole site; a KB article is a few hundred KB,
    and this runs across a whole bank, so the ingest's headroom is only an
    availability risk here.
    """
    from src.data_manager.collectors.scrapers.sitemap_source import fetch_sitemap_text

    def fetch(url: str) -> str:
        return fetch_sitemap_text(url, max_bytes=MAX_PAGE_BYTES)

    return fetch


def read_source_lines(path: str) -> List[str]:
    try:
        return Path(path).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise OperationalError(f"cannot read {path}: {exc}") from exc


def _print_group(title: str, lines: Sequence[str], stream=sys.stdout) -> None:
    print(f"\n{title}", file=stream)
    for line in lines:
        print(f"  {line}", file=stream)


def _sitemap_policy(args: argparse.Namespace, source_lines: Sequence[str]):
    """Build the sitemap policy the live-inventory expansion runs under.

    The floor is the completeness guard, so it must match the deployment's.
    ``SitemapPolicy``'s own default is ``min_pages=1``: under it a truncated
    sitemap (a handful of pages instead of a few hundred) expands
    "successfully", the inventory reads as complete, and every bank URL missing
    from that partial response is reported as an orphan. FASRC configures 150.
    So when the source list actually contains a ``sitemap-`` line, refuse to run
    without an explicit floor rather than silently judging against 1.
    """
    from src.data_manager.collectors.scrapers.sitemap_source import SitemapPolicy

    has_sitemap = any(line.strip().startswith("sitemap-") for line in source_lines)
    if has_sitemap and args.min_pages is None:
        raise OperationalError(
            "refusing to judge orphans against a sitemap without an explicit "
            "--min-pages floor (match the deployment's sitemap.min_pages). The "
            "default floor is 1, so a truncated sitemap would read as complete "
            "and every unlisted bank row would look deleted."
        )
    policy = SitemapPolicy()
    if args.min_pages is not None:
        policy.min_pages = args.min_pages
    if args.max_pages is not None:
        policy.max_pages = args.max_pages
    if args.allowed_hosts:
        policy.allowed_hosts = list(args.allowed_hosts)
    return policy


def require_gap(url: str, docs, bank, action: str):
    """Resolve a URL to the corpus doc for a page that is a *current gap*.

    Both dispositions of a reviewed gap go through here. `--propose` says "this
    page earns a question" and `--decline` says "it does not"; neither statement
    is one an operator is in a position to make about a page that is already
    covered, that the tool cannot classify, or that the corpus does not hold.
    Recording either would put a claim in the ledger — or a duplicate row in the
    bank — that nobody actually reviewed.

    Reuses `reconcile`, so this guard, the coverage report and orphan detection
    cannot disagree about what "already covered" means.
    """
    canonical = canonical_url(url)
    doc = {d.url: d for d in docs}.get(canonical) if canonical else None
    if doc is None or canonical is None:
        raise OperationalError(
            f"{url} is not in the retrievable corpus, so it is not a gap to "
            f"{action} (is it ingested and embedded?)"
        )
    against_bank = reconcile([canonical], bank_source_urls(bank))
    if against_bank.matched:
        raise OperationalError(
            f"{canonical} is already covered by a bank row — `--{action}` is a "
            "decision about a gap."
        )
    if against_bank.near_misses:
        near = against_bank.near_misses[0]
        raise OperationalError(
            f"{canonical} is a slug near-miss for {', '.join(near.candidates)} — "
            f"reconcile it before you {action} it; the tool cannot tell yet "
            "whether this page is already covered."
        )
    return doc


def run_decline(args: argparse.Namespace, docs, bank) -> int:
    """Record an operator's dismissal of a page. Touches the ledger, not the bank.

    The whole read-merge-write runs under an exclusive lock, so a decline
    another session records in the meantime is merged rather than clobbered.
    """
    if not args.ledger:
        raise OperationalError("--decline needs --ledger <path> to record the decision")
    require_gap(args.decline, docs, bank, "decline")
    with ledger_lock(args.ledger):
        entries = read_ledger(args.ledger)
        try:
            stamped = with_decline(
                entries,
                args.decline,
                reason=args.reason or "",
                at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
        except ValueError as exc:
            raise OperationalError(f"cannot record the decline: {exc}") from exc
        if len(stamped) == len(entries):
            print(f"already declined: {args.decline}")
            return 0
        write_ledger(args.ledger, stamped)
    print(f"declined: {args.decline} -> {args.ledger}")
    return 0


def run_undecline(args: argparse.Namespace) -> int:
    """Reverse a decline. Without this a dismissal is permanent.

    Deliberately does NOT require the URL to be a gap: the point is to undo a
    record, and a page whose status has since changed is exactly the one an
    operator needs to be able to clear.
    """
    if not args.ledger:
        raise OperationalError(
            "--undecline needs --ledger <path> — the record to clear lives there"
        )
    with ledger_lock(args.ledger):
        entries = read_ledger(args.ledger)
        try:
            kept = without_decline(entries, args.undecline)
        except ValueError as exc:
            raise OperationalError(f"cannot clear the decline: {exc}") from exc
        if len(kept) == len(entries):
            print(f"not declined: {args.undecline} — nothing to clear")
            return 0
        write_ledger(args.ledger, kept)
    print(f"undeclined: {args.undecline} -> {args.ledger}")
    return 0


def run_propose(args: argparse.Namespace, docs, bank, declined) -> int:
    """Draft candidates for the one page the operator greenlit by naming it.

    Greenlighting is a decision about a *gap*. A page a row already grounds on,
    or one the tool cannot classify because of a slug near-miss, is refused: the
    first manufactures a duplicate question that reads as valid once pasted in,
    and the second drafts on top of an unknown that may already be covered under
    the moved slug. Both refusals name the row so the refusal is actionable.
    """
    if not args.model:
        raise OperationalError("--propose needs --model <provider/model> to draft with")
    doc = require_gap(args.propose, docs, bank, "propose")
    if doc.url in declined:
        # Drafting while the decline stands would leave the page suppressed for
        # good: the candidates are unapplied, so nothing covers the page, and the
        # stale entry keeps hiding it from every later run.
        raise OperationalError(
            f"{doc.url} is declined in the ledger. Clear it first with "
            f"`--undecline {doc.url}` — otherwise the drafts go unapplied and the "
            "page stays suppressed."
        )

    page_text = read_persisted_document(doc, args.data_path)
    try:
        proposal = propose_candidates(
            doc.url, page_text, build_ask_llm(args.model), count=args.count
        )
    except ProposalError as exc:
        raise OperationalError(str(exc)) from exc

    if proposal.rejected:
        _print_group(
            "rejected candidates (not written anywhere)",
            [f"{r.reason}: {json.dumps(r.raw)[:120]}" for r in proposal.rejected],
            stream=sys.stderr,
        )
    if not proposal.candidates:
        print(
            f"no usable candidate survived for {doc.url} — nothing proposed.",
            file=sys.stderr,
        )
        return 1

    print(
        f"\n{len(proposal.candidates)} draft candidate(s) for {doc.url} — review, "
        "then paste into the bank by hand. This run wrote nothing."
    )
    print(json.dumps([c.as_row() for c in proposal.candidates], indent=2))
    return 0


def run_coverage(args: argparse.Namespace) -> int:
    bank = load_bank(args.bank)
    if args.undecline:
        # Pure reversal: no corpus needed to clear a record.
        return run_undecline(args)
    declined = declined_urls(read_declines(read_ledger(args.ledger)))

    if not args.corpus_json and not args.pg_dsn:
        raise OperationalError("coverage needs --corpus-json or --pg-dsn")
    if args.corpus_json:
        fetch_rows = corpus_rows_from_json(args.corpus_json)
    else:
        fetch_rows = corpus_rows_from_postgres(args.pg_dsn)
    corpus = read_corpus_docs(fetch_rows)
    if args.decline:
        return run_decline(args, corpus, bank)
    if args.propose:
        return run_propose(args, corpus, bank, declined)

    docs = filter_docs(
        corpus,
        source_type=args.source_type,
        parent=args.parent,
        path_glob=args.path_glob,
    )
    report = find_coverage_gaps(docs, bank, declined=declined)

    print(
        f"corpus: {len(docs)} pages | covered: {len(report.covered)} | "
        f"{len(report.gaps)} gaps | {len(report.needs_reconciliation)} need "
        f"reconciliation | {len(report.suppressed)} declined (suppressed)"
    )
    if report.gaps:
        for parent, group in group_by_parent(report.gaps).items():
            _print_group(f"gaps — {parent} ({len(group)})", [d.url for d in group])
    if report.needs_reconciliation:
        _print_group(
            "needs reconciliation (slug near-miss — confirm before treating as a gap)",
            [
                f"{near.url}  ~  {', '.join(near.candidates)}"
                for near in report.needs_reconciliation
            ],
        )
    if report.suppressed:
        # Named, never silently filtered: a hidden page reads as a clean report.
        _print_group(
            "declined earlier (suppressed from gaps — in the ledger)",
            [d.url for d in report.suppressed],
        )
    return 0


def run_orphans(args: argparse.Namespace) -> int:
    from src.data_manager.collectors.scrapers.sitemap_source import fetch_sitemap_text

    bank = load_bank(args.bank)
    source_lines = read_source_lines(args.sources)
    policy = _sitemap_policy(args, source_lines)
    inventory = build_live_inventory(source_lines, fetch_sitemap_text, policy)
    report = find_orphans(bank, inventory)

    if report.abstained:
        # An incomplete inventory is an OPERATIONAL failure, not a finding: no
        # orphan analysis happened. Exiting zero here would let a cron read
        # "nothing was flagged" as healthy and hide a broken inventory forever.
        print(
            "ABSTAINED — the live source inventory is incomplete, so nothing was "
            "flagged. Orphan detection needs a complete inventory: a partial one "
            "would make every unlisted page look deleted.",
            file=sys.stderr,
        )
        _print_group("why", report.reasons, stream=sys.stderr)
        return 1

    print(
        f"live inventory: {len(inventory.urls)} URLs | {len(report.orphans)} orphans | "
        f"{len(report.out_of_scope)} out of scope | "
        f"{len(report.needs_reconciliation)} need reconciliation | "
        f"{len(inventory.unsupported)} source(s) not enumerable"
    )
    if inventory.unsupported:
        _print_group(
            "not enumerable (fan-out source — rows on these hosts are never judged)",
            inventory.unsupported,
        )
    if report.orphans:
        _print_group(
            "orphans (grounding page gone from the live KB — propose, never delete)",
            [
                f"row {o.row_index}: {', '.join(o.urls)}  — {o.user_input[:70]}"
                for o in report.orphans
            ],
        )
    if report.out_of_scope:
        _print_group(
            "out of scope (host the inventory does not cover — never judged)",
            report.out_of_scope,
        )
    if report.needs_reconciliation:
        _print_group(
            "needs reconciliation (slug near-miss)",
            [
                f"{n.url}  ~  {', '.join(n.candidates)}"
                for n in report.needs_reconciliation
            ],
        )
    return 0


def _drift_label(check) -> str:
    """One line describing a source check, with any advisory verdict attached."""
    line = check.url
    if check.verdict is not None:
        line += f"  [{check.verdict.verdict}]"
        if check.verdict.explanation:
            line += f" {check.verdict.explanation}"
    return line


def run_drift(args: argparse.Namespace) -> int:
    """Re-hash every locked row's sources and report what moved.

    Advisory end to end: the bank file is opened read-only, and neither
    `reference` nor `status` nor `source_hashes` is ever rewritten. A confirmed
    row that drifted is a question for a human, and re-confirming it is the same
    human act as locking it was.
    """
    bank = load_bank(args.bank)
    ask_llm = build_ask_llm(args.model) if args.model else None
    report = find_drift(
        bank,
        build_fetch_html(),
        ask_llm=ask_llm,
        allowed_hosts=args.allowed_hosts or (),
    )

    if report.abstained:
        # Every fetch failed, so no page was actually read. Reporting "no drift"
        # would be a clean bill of health nothing was checked for.
        print(
            "ABSTAINED — every source fetch failed, so nothing was checked. "
            "Reporting no drift here would be a false clean over the whole bank.",
            file=sys.stderr,
        )
        _print_group("why", report.reasons, stream=sys.stderr)
        return 1

    print(
        f"locked rows: {report.checked_rows} checked | {report.skipped_rows} skipped "
        f"(draft or source-less) | {len(report.drifted)} drifted | "
        f"{len(report.unbaselined)} without a baseline | "
        f"{len(report.incomparable)} incomparable | "
        f"{len(report.unreachable)} unreachable"
    )

    if report.drifted:
        _print_group(
            "drift (source changed since the reference was confirmed — advisory)",
            [
                f"row {row.row_index}: {_drift_label(c)}  — {row.user_input[:60]}"
                for row in report.drifted
                for c in row.changed
            ],
        )
    # Each bucket below is a source the tool could NOT judge. They are printed
    # separately, and never folded into "unchanged", because a page that failed
    # to be checked must not read as a page that is fine.
    _print_state(
        report,
        DRIFT_UNBASELINED,
        "no baseline recorded (locked, but no source_hashes entry — not checked)",
    )
    _print_state(
        report,
        DRIFT_INCOMPARABLE,
        "incomparable baseline (stored hash is not a sha256: digest — not checked)",
    )
    _print_state(
        report,
        DRIFT_UNREACHABLE,
        "unreachable this run (NOT evidence the page is unchanged)",
    )
    _print_state(
        report,
        DRIFT_REFUSED,
        "refused by the fetch policy (never contacted, never sent to a model)",
    )

    stale = [
        f"row {row.row_index}: {', '.join(row.stale_baselines)}"
        for row in report.rows
        if row.stale_baselines
    ]
    if stale:
        _print_group(
            "baselines kept for URLs the row no longer cites (edit `sources`?)", stale
        )

    if args.show_text:
        _print_evidence(report)
    elif report.drifted:
        print(
            "\nRe-run with --show-text to see the page text behind each finding "
            "(and, with --model, the text the verdict was formed on)."
        )
    if args.print_hashes:
        _print_hashes(report)
    elif report.unbaselined or report.incomparable:
        print(
            "\nRe-run with --print-hashes to get paste-ready `source_hashes` blocks "
            "for the rows above."
        )
    return 0


def _print_state(report, state: str, title: str) -> None:
    """Print every check in one unjudgeable state, tagged with its row."""
    lines = [
        f"row {row.row_index}: {c.url}" + (f" — {c.detail}" if c.detail else "")
        for row in report.rows
        for c in row.checks
        if c.state == state
    ]
    if lines:
        _print_group(title, lines)


def _print_evidence(report) -> None:
    """Print the fetched text behind each finding, bounded and delimited.

    Opt-in rather than default: the report is a work list, and a page body per
    changed row would bury it. But a verdict formed on text the operator cannot
    see is not reviewable — the model gets the page and the human gets a
    sentence — so the text has to be reachable in one flag.

    The tool stores only a hash of the old page, never its text, so this is the
    NEW page rather than a diff. That is the deliberate cost of keeping no state
    file between runs.
    """
    for row in report.drifted:
        for check in row.changed:
            print(f"\n--- {check.url} (as fetched now) ---")
            print(check.fresh_text)
            print(f"--- end {check.url} ---")


def _print_hashes(report) -> None:
    """Emit a paste-ready `source_hashes` block for every row with a fresh hash.

    The bank is never written, so this is the whole path by which a baseline gets
    recorded: the tool computes, a human pastes. Without it `source_hashes` could
    never be populated and the tripwire would sit inert.
    """
    for row in report.rows:
        fresh = {c.url: c.fresh for c in row.checks if c.fresh}
        if not fresh:
            continue
        print(f"\nrow {row.row_index} — {row.user_input[:60]}")
        print(json.dumps({"source_hashes": fresh}, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only coverage / orphan detection for the RAGAS golden-set bank. "
            "Never writes the bank."
        )
    )
    sub = parser.add_subparsers(dest="command")

    def add_bank(sp):
        sp.add_argument("--bank", required=True, help="Path to the bank JSON array.")

    coverage = sub.add_parser("coverage", help="Ingested pages no bank row grounds on.")
    add_bank(coverage)
    # Not `required=True`: `--decline` is pure bookkeeping and needs no corpus.
    # `run_coverage` enforces the requirement for the passes that do read one.
    source = coverage.add_mutually_exclusive_group()
    source.add_argument("--corpus-json", help="JSON dump of `documents` rows.")
    source.add_argument("--pg-dsn", help="Postgres DSN for the live catalog.")
    coverage.add_argument("--source-type", help="Only this source_type (web/git/…).")
    coverage.add_argument("--parent", help="Only this parent source (host or repo).")
    coverage.add_argument("--path-glob", help="Only URLs matching this glob.")
    decision = coverage.add_mutually_exclusive_group()
    decision.add_argument(
        "--propose",
        metavar="URL",
        help=(
            "Draft candidate questions for this ONE greenlit page and print them "
            "for review. Writes nothing — pasting a candidate into the bank is a "
            "separate human step."
        ),
    )
    decision.add_argument(
        "--decline",
        metavar="URL",
        help=(
            "Record that this gap does not earn a question; needs --ledger and a "
            "corpus. Refused for a page that is covered, a near-miss, or absent."
        ),
    )
    decision.add_argument(
        "--undecline",
        metavar="URL",
        help="Clear an earlier decline so the page becomes a gap again.",
    )
    coverage.add_argument("--reason", help="Why the page was declined (free text).")
    coverage.add_argument(
        "--ledger",
        help=(
            "Decision-ledger JSON. Records DECLINES only — covered-ness is always "
            "re-derived from the bank, so a drafted-but-unapplied page stays a gap."
        ),
    )
    coverage.add_argument(
        "--model",
        help="`provider/model` used to draft candidates. Required with --propose.",
    )
    coverage.add_argument(
        "--data-path",
        help=(
            "Deployment data root that `documents.file_path` resolves against. "
            "Required with --propose: candidates are grounded in the persisted "
            "(indexed) document, never in a live re-fetch."
        ),
    )
    coverage.add_argument(
        "--count",
        type=int,
        default=3,
        help="How many candidates to ask for per greenlit page (default 3).",
    )
    coverage.set_defaults(func=run_coverage)

    orphans = sub.add_parser("orphans", help="Rows whose grounding page is gone.")
    add_bank(orphans)
    orphans.add_argument(
        "--sources",
        required=True,
        help="Source list; `sitemap-` lines are expanded live.",
    )
    orphans.add_argument(
        "--min-pages",
        type=int,
        help=(
            "Sitemap completeness floor — match the deployment's "
            "data_manager.sources.links.sitemap.min_pages (FASRC: 150). Required "
            "when the source list contains a `sitemap-` line; without it a "
            "truncated sitemap reads as complete and yields false orphans."
        ),
    )
    orphans.add_argument(
        "--max-pages",
        type=int,
        help="Sitemap cap — match the deployment's sitemap.max_pages.",
    )
    orphans.add_argument(
        "--allowed-hosts",
        nargs="*",
        help="Extra hosts the sitemap may emit — the deployment's allowed_hosts.",
    )
    orphans.set_defaults(func=run_orphans)

    drift = sub.add_parser("drift", help="Locked rows whose grounding page changed.")
    add_bank(drift)
    drift.add_argument(
        "--model",
        help=(
            "`provider/model` asked whether the stored reference still holds, for "
            "sources whose hash moved. Optional: without it the run is the cheap "
            "hash tripwire alone, which is still a real finding."
        ),
    )
    drift.add_argument(
        "--allowed-hosts",
        nargs="*",
        help=(
            "Restrict which hosts drift may contact. Without it, any public host "
            "a row cites is fetched; loopback/private/link-local and non-http(s) "
            "targets are always refused."
        ),
    )
    drift.add_argument(
        "--show-text",
        action="store_true",
        help=(
            "Print the fetched page text behind each finding. The tool keeps no "
            "old text, so this is the new page, not a diff."
        ),
    )
    drift.add_argument(
        "--print-hashes",
        action="store_true",
        help=(
            "Print a paste-ready `source_hashes` block per row. The tool never "
            "writes the bank, so this is how a baseline gets recorded."
        ),
    )
    drift.set_defaults(func=run_drift)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if not getattr(args, "func", None):
        print("error: a subcommand is required (coverage | orphans)", file=sys.stderr)
        return 2
    try:
        return args.func(args)
    except OperationalError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

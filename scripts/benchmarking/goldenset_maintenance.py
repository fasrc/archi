#!/usr/bin/env python
"""Read-only maintenance passes over the RAGAS golden-set question bank.

Answers three questions an operator otherwise has to eyeball by hand:

- ``coverage`` — which ingested KB pages does no bank row ground against?
- ``orphans`` — which bank rows cite a page the live KB no longer publishes?
- ``drift`` — which confirmed rows were grounded in a page that has since changed?

and ``report``, which runs all three in one unattended pass for a cron.

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
        --bank <bank.json> --allowed-hosts docs.rc.fas.harvard.edu \\
        [--model anthropic/claude-sonnet-5] [--show-text] [--print-hashes]

    # all three passes in one read-only run (the cron line)
    python scripts/benchmarking/goldenset_maintenance.py report \\
        --bank <bank.json> --pg-dsn <dsn> --sources config/lists/sources.list \\
        --allowed-hosts docs.rc.fas.harvard.edu --min-pages 150 --ledger <ledger.json>
"""

from __future__ import annotations

import argparse
import json
import os
import stat
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

from src.utils.benchmark_schema import (  # noqa: E402  # isort: skip
    bank_status_counts,
    normalize_bank,
)
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


def _resolved_target(path: str) -> Path:
    """The real file a write should land on, with the final symlink followed.

    `os.replace` does NOT follow a symlink on its destination — it swaps the
    LINK's own directory entry. So an atomic write against a symlinked output
    path replaces the operator's stable path with a regular file and leaves the
    real file untouched and stale. A deployment names its report through exactly
    such a path (`current -> releases/42/report.json`), and `open(path, "w")` —
    what the summary write used before it became atomic — follows the link and
    updates the referent. That is the behaviour to keep.

    Resolving BEFORE the temp file is created is what makes the commit possible
    at all: creating the temp beside the *link* and replacing onto the
    *referent* would cross a mount whenever the link does, and `rename(2)`
    answers that with EXDEV instead of committing.

    `realpath` on a path that does not exist yet still resolves the parent
    chain, so a first run and a dangling symlink both land where `open` would
    have put them.
    """
    return Path(os.path.realpath(path))


def _output_target(path: str, label: str) -> Path:
    """The file a write must land on: final symlink followed, a loop refused.

    `_resolved_target` is deliberately **total**, and for an INPUT that is the
    right trade: an unresolvable `--bank` reaches the read, where `ELOOP` is
    reported as a failed pass with a summary written, instead of ending the run
    on a traceback before it starts.

    For an OUTPUT it is the wrong half of that trade. `realpath` gives up on a
    cycle and hands the unresolved link back; `os.replace` does not follow a
    symlink on its destination; so the commit swaps the link's own directory
    entry and turns the operator's broken configuration into a plausible regular
    file — where the pre-atomic `open(path, "w")` raised `ELOOP` and changed
    nothing. Losing the diagnosis is the smaller half: a summary sitting where a
    link belongs reads as a healthy run to whoever looks next.

    A resolution that is itself STILL a symlink is precisely the signal that
    resolution gave up, which is why that is the test. A dangling link is not
    that — `realpath` follows it to a name that does not exist yet, exactly
    where `open` would have created the file.
    """
    resolved = _resolved_target(path)
    if os.path.islink(resolved):
        raise OperationalError(
            f"cannot write {label} {resolved}: the path is a symlink loop, so it "
            "resolves to nothing. Committing atomically would replace the link "
            "with a regular file and destroy the configuration silently. Repair "
            "the link, or name the file directly."
        )
    return resolved


def pinned_output(args: argparse.Namespace, flag: str) -> str:
    """The resolution `reject_aliased_outputs` validated — not a fresh lookup.

    The alias refusal resolves every declared path before the first read. Left
    at that, each writer resolved its own path again afterwards, and for
    `report` that is minutes of network passes later. A symlinked output
    retargeted inside that window bypasses the refusal without defeating it:
    point `--summary-json`'s stable link at the bank once the run is under way
    and `os.replace` lands on a file the check never saw. The same window sits
    before `ledger_lock`, one lookup earlier than the pinning inside the
    transaction can reach.

    This is not a general cure for the race — anyone able to retarget the link
    can swap the resolved file too. It is what makes the refusal hold for the
    whole run rather than for the instant it ran.

    Falls back to resolving when nothing is pinned, so the writers stay callable
    on their own.
    """
    pinned = getattr(args, "pinned_outputs", None) or {}
    if flag in pinned:
        return str(pinned[flag])
    return str(_resolved_target(str(getattr(args, flag))))


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

    The sidecar is named after the ledger's RESOLVED path, matching what
    `write_ledger` actually replaces. Named after the path the operator typed, a
    run reaching the ledger through a symlink and one reaching it directly would
    take two different locks, serialise against nothing, and lose a decline to
    the second replace — the exact update this lock exists to protect.

    That resolution is done ONCE and **yielded**, and the caller must read and
    write the path it hands back. Resolving again at each step reopens the same
    hole from the other side: a deployment that retargets the advertised stable
    symlink after the lock is taken leaves this command holding the old
    referent's sidecar while it reads and replaces the new one, where a
    concurrent command is serialising on that file's own lock. One resolution per
    transaction is what makes the lock, the read and the write the same file by
    construction rather than by three lookups agreeing.

    `fcntl` is POSIX-only, and where it is missing this **refuses** rather than
    proceeding with a warning. A warning is not a mitigation — the lost update
    happens either way, and the operator has no way to notice. Only ledger
    mutation takes this path; coverage, orphans and `--propose` are read-only and
    still run.
    """
    resolved = _resolved_target(path)
    lock_path = Path(f"{resolved}.lock")
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
        yield str(resolved)
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


def _copy_xattrs(target: Path, tmp_name: str) -> None:
    """Carry the target's extended attributes over, POSIX ACLs included.

    An ACL is not a mode bit: on Linux it lives in the `system.posix_acl_access`
    xattr, so a file whose grant to a monitor is `setfacl -m u:monitor:r` keeps
    that grant nowhere `chmod` can reach it. Per-attribute failures are skipped
    rather than raised — `security.*` and `trusted.*` need privileges this tool
    should not assume, and losing one of those is not worth losing the write.
    """
    if not hasattr(os, "listxattr"):  # pragma: no cover - non-Linux
        return
    try:
        names = os.listxattr(str(target))
    except OSError:  # pragma: no cover - filesystem carries no xattrs
        return
    for name in names:
        try:
            os.setxattr(tmp_name, name, os.getxattr(str(target), name))
        except OSError:  # pragma: no cover - a namespace we may not write
            continue


def _copy_ownership(source: os.stat_result, tmp_name: str) -> None:
    """Move the replacement onto the target's uid/gid, where that is a thing.

    `os.chown` is POSIX-only and simply **absent** elsewhere, so calling it
    unguarded raises `AttributeError` — which is not an `OSError`, so it escapes
    the writer's handler before the temp file is unlinked. `_copy_xattrs` already
    draws this boundary with `hasattr(os, "listxattr")`; the same boundary
    belongs here.

    The failure is quiet in the worst way: `_preserve_access` returns early when
    there is no target to copy from, so *creating* a summary works and every
    refresh after it crashes.
    """
    if not hasattr(os, "chown"):  # pragma: no cover - non-POSIX
        return
    try:
        os.chown(tmp_name, source.st_uid, source.st_gid)
    except OSError:
        # Not privileged enough to move the uid. The GROUP can still be set by
        # its own member, and that is the half that usually carries the grant.
        try:
            os.chown(tmp_name, -1, source.st_gid)
        except OSError:  # pragma: no cover - not a member of the target's group
            pass


def _close_quietly(handle) -> None:
    """Close a staged handle without letting the close become the failure.

    Called from the writers' cleanup, where an exception is usually already in
    flight. `close()` flushes, so on a full disk it raises the same `ENOSPC` that
    brought us here — and that second error would replace the first, escape
    before the temp file is unlinked, and end the run on a traceback.
    """
    if handle is None:
        return
    try:
        handle.close()
    except (OSError, ValueError):
        pass


def _close_fd_quietly(fd: Optional[int]) -> None:
    """Close a directory descriptor without letting the close become the failure.

    `_close_quietly`'s sibling, for the one cleanup step that sat outside that
    boundary. `write_ledger`'s `finally` runs while the write's own
    `OperationalError` is already in flight, and an `OSError` from `close(2)`
    raised there propagates out of the `finally` and REPLACES it — leaving the
    operator holding a descriptor error instead of the diagnosis of the disk
    that failed their write. After the commit it is worse still: it would either
    replace `LedgerNotDurable` or manufacture a raw `OSError` out of a run that
    entirely succeeded.
    """
    if fd is None:
        return
    try:
        os.close(fd)
    except OSError:
        pass


def _discard_quietly(tmp_name: str) -> None:
    """Remove an uncommitted temp file. A failure here has nothing left to say."""
    if not tmp_name:
        return
    try:
        os.unlink(tmp_name)
    except OSError:  # pragma: no cover - already gone
        pass


def _preserve_access(target: Path, tmp_name: str) -> None:
    """Carry an existing target's access metadata onto its replacement.

    `tempfile.mkstemp` creates its file 0600, owned by the writing process, with
    no extended attributes — and `os.replace` installs all of that along with the
    contents. So a replace-in-place write silently revokes any access an operator
    had granted on the target: a `--summary-json` health file readable by a
    monitor running as another Unix user goes unreadable on the very next report
    run, and the monitor cannot tell that apart from "nothing to report".

    Three carriers, because any one of them alone can BE the grant, and copying
    only the first leaves the other two revoked:

    - the mode bits;
    - the owning uid/gid — `0640 archi:monitor` grants through its GROUP, and
      mkstemp's file is `archi:archi`, so mode alone still locks the monitor out;
    - the POSIX ACL, which is an xattr (see `_copy_xattrs`).

    Ordered chown → chmod → xattrs. chown before chmod because chowning clears
    setuid/setgid; the ACL last because it encodes the base permission bits too,
    so a chmod after it would rewrite its mask.

    Every step is best-effort. An unprivileged run cannot move a file to another
    uid, and not every filesystem carries xattrs — failing the write over
    metadata would trade a cosmetic loss for a lost health signal, which is the
    worse outcome.

    Applied only when the target already exists. Picking a mode for a NEW file is
    a policy decision this tool has no business making, and mkstemp's 0600 is the
    right default for one that can carry error text.
    """
    try:
        source = target.stat()
    except OSError:  # no target yet, or it raced away — keep mkstemp's 0600
        return
    _copy_ownership(source, tmp_name)
    try:
        os.chmod(tmp_name, stat.S_IMODE(source.st_mode))
    except OSError:  # pragma: no cover - a mode-less filesystem
        pass
    _copy_xattrs(target, tmp_name)


def _warn_if_multiply_linked(target: Path, label: str) -> None:
    """Say which other name this replacement is about to leave behind.

    `os.replace` installs a NEW inode under the target's name. Any other hard
    link to the old inode keeps it, and keeps the previous contents — a monitor
    reading that second name sits on a healthy snapshot forever while every run
    reports success. `realpath` cannot see this: hard links are not a chain to
    follow, they are equal names for one inode.

    Neither obvious remedy is available. Truncating in place would put every
    name back on one inode and reopen the partial-write window this file's
    atomicity exists to close — and it would not help a consumer holding an open
    descriptor across the write, which goes stale the same way. Refusing the
    write is worse: the summary IS the health signal, so a run that declines to
    write it leaves the monitor on the previous healthy file indefinitely, which
    is the failure the summary contract exists to close.

    So the write commits and the run names the decoupled path. A symlink is the
    shape that actually works here, because it IS followed.

    Both halves are contained, because this runs INSIDE the writers' `try` and a
    diagnostic must not be able to fail the write it is describing. An
    unwritable stderr (`2>&-` gives `EBADF`) would otherwise be caught as an
    `OSError` by the writer, delete the staged temp file, and report "cannot
    write" for a write that was ready to commit; a stderr closed under the
    process raises `ValueError`, which that handler does not catch at all, so it
    would escape the writer entirely and leave the temp file as litter.
    """
    try:
        links = target.stat().st_nlink
    except OSError:  # pragma: no cover - no target yet, or it raced away
        return
    if links < 2:
        return
    try:
        print(
            f"warning: {label} {target} has {links - 1} other hard link(s). "
            "Committing it atomically gives this name a new inode, so those names "
            "keep the previous contents and go stale silently. Point every "
            "consumer at this path, or reach it through a symlink, which is "
            "followed.",
            file=sys.stderr,
        )
    except (OSError, ValueError):
        return  # an unsayable warning is not a reason to fail the write


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
    target = _output_target(path, "the ledger")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OperationalError(f"cannot write ledger {path}: {exc}") from exc

    payload = json.dumps(entries, indent=2, ensure_ascii=False) + "\n"
    handle = None
    tmp_name = ""
    dir_fd = None
    committed = False
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
        _preserve_access(target, tmp_name)
        _warn_if_multiply_linked(target, "the ledger")
        os.replace(tmp_name, target)
        committed = True
    except OSError as exc:
        raise OperationalError(f"cannot write ledger {path}: {exc}") from exc
    finally:
        # In a `finally`, and keyed on `committed`, because the handler above
        # only sees OSError. Anything else raised in the block — an absent
        # `os.chown`, an unsayable warning — would otherwise skip the cleanup
        # and leave the staged temp file behind.
        _close_quietly(handle)
        if not committed:
            _discard_quietly(tmp_name)
            _close_fd_quietly(dir_fd)
            dir_fd = None

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
        _close_fd_quietly(dir_fd)


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

    Two of that fetcher's defaults are overridden rather than inherited:

    - **TLS is verified.** `fetch_sitemap_text` defaults to `verify=False`.
      Inheriting that would let anyone on the network path substitute page
      content — manufacturing drift findings, steering the advisory verdict, and
      putting text of their choosing into a prompt sent to the model provider.
      A private CA goes in `REQUESTS_CA_BUNDLE`; there is deliberately no flag to
      turn verification off, because such a flag ends up in the cron line.
    - **The connection may never leave HTTPS.** Verification only protects a hop
      that is still TLS, and the fetcher's redirect guard compares hosts, not
      schemes — so an allowlisted page that redirects to `http://` on its own
      host would be read in the clear with `verify=True` still set. `find_drift`
      refuses a plaintext `sources` URL before dialing; this closes the redirect
      the policy layer cannot see.
    - **The body cap drops to `MAX_PAGE_BYTES`.** The ingest's 64 MiB ceiling is
      sized for a whole site's sitemap index; a KB article is a few hundred KB,
      and this runs across a whole bank.
    """
    from src.data_manager.collectors.scrapers.sitemap_source import fetch_sitemap_text

    def fetch(url: str) -> str:
        return fetch_sitemap_text(
            url, verify=True, max_bytes=MAX_PAGE_BYTES, require_https=True
        )

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


def _record(args: argparse.Namespace, **counts: Any) -> None:
    """Note a pass's finding counts for `report --summary-json`, if asked.

    The exit code carries only two states — the run happened, or it broke — and
    the cron contract pins findings to zero. So a wrapper keying notification on
    the exit status cannot tell "clean" from "there is work to do", which is the
    one state the job exists to surface. This is that third signal, kept
    machine-readable so nothing has to parse the human report.

    A no-op unless `report` asked for it: the single-pass subcommands are
    unaffected and write nothing.
    """
    sink = getattr(args, "summary_sink", None)
    if sink is not None:
        sink.update(counts)


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
    with ledger_lock(pinned_output(args, "ledger")) as ledger_path:
        entries = read_ledger(ledger_path)
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
        write_ledger(ledger_path, stamped)
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
    with ledger_lock(pinned_output(args, "ledger")) as ledger_path:
        entries = read_ledger(ledger_path)
        try:
            kept = without_decline(entries, args.undecline)
        except ValueError as exc:
            raise OperationalError(f"cannot clear the decline: {exc}") from exc
        if len(kept) == len(entries):
            print(f"not declined: {args.undecline} — nothing to clear")
            return 0
        write_ledger(ledger_path, kept)
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
    _record(
        args,
        gaps=len(report.gaps),
        needs_reconciliation=len(report.needs_reconciliation),
    )

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

    _record(
        args,
        orphans=len(report.orphans),
        # Its own key: coverage records a near-miss bucket too, and one
        # `update()` overwriting the other would silently drop a finding.
        orphans_needs_reconciliation=len(report.needs_reconciliation),
    )
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
        allowed_hosts=args.allowed_hosts,
    )

    if report.abstained:
        # Every fetch failed, so no page was actually read. Reporting "no drift"
        # would be a clean bill of health nothing was checked for.
        print(
            "ABSTAINED — no source was read at all (every one was unreachable or "
            "refused by the fetch policy), so nothing was checked. Reporting no "
            "drift here would be a false clean over the whole bank.",
            file=sys.stderr,
        )
        _print_group("why", report.reasons, stream=sys.stderr)
        return 1

    _record(
        args,
        drifted=len(report.drifted),
        # Which question the run actually answered. A hash mismatch says the
        # bytes moved; whether the recorded ANSWER is now wrong is a different
        # question, and without a model nothing asked it. Recording that keeps a
        # tripwire-only run from reading as a completed staleness check.
        drift_check="reference-compared" if ask_llm is not None else "hash-only",
        # A source the tool WANTED to check and could not. `find_drift` abstains
        # only when nothing at all was read, so one readable page makes the pass
        # "succeed" — and counting drifted rows alone would let a run that
        # checked 1 of 50 sources summarise as clean.
        unchecked_sources=(
            len(report.unbaselined) + len(report.incomparable) + len(report.unreachable)
        ),
        # Counted separately from `unchecked_sources` because the remedy differs
        # — fix the allowlist, not the page — but it notifies just the same. A
        # host absent from `--allowed-hosts` is OMISSION, not intent: the
        # operator listed the hosts they thought of, and a row added later
        # citing a new host would then go unchecked forever with nobody told.
        # Reading "not listed" as "do not check" would be silence-by-omission,
        # which is the failure every other bucket here exists to prevent.
        refused_sources=len(report.refused),
    )
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

    if report.drifted and ask_llm is None:
        # Beside the findings it qualifies, not on every run: a clean report has
        # nothing to be uncertain about.
        print(
            "\nNOTE: the stored `reference` was NOT compared against the new page — "
            "this run was the hash tripwire only. It establishes that the source "
            "moved, not that the recorded answer is now wrong. Re-run with "
            "--model <provider/model> for that, or review by hand with --show-text."
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

    A block must therefore be **complete for the row**, because pasting it
    replaces the whole map. A source that could not be read this run carries its
    existing baseline forward; emitting only the sources that happened to answer
    would quietly delete the others' confirmation history — from output that
    invited the paste. Where a source has neither a fresh nor a stored hash there
    is nothing to carry, so the block is labelled INCOMPLETE rather than
    presented as safe.

    Baselines for URLs the row no longer cites are deliberately NOT carried
    forward; they are reported separately as stale, and dropping them is the
    point of pasting.

    Emitting nothing is explained rather than left silent. Blocks come from the
    same locked-only pass as the rest of drift, so asking for a draft row's
    baseline produces an empty run — right place, wrong step — and unexplained
    silence reads as a broken tool.
    """
    emitted = 0
    for row in report.rows:
        carried = {
            c.url: (c.fresh or c.stored) for c in row.checks if c.fresh or c.stored
        }
        if not carried:
            continue
        emitted += 1
        missing = [c.url for c in row.checks if not (c.fresh or c.stored)]
        print(f"\nrow {row.row_index} — {row.user_input[:60]}")
        if missing:
            print(
                "  # INCOMPLETE — no hash available for: "
                + ", ".join(missing)
                + "\n  # Pasting this as-is would drop those sources. Re-run once "
                "they are reachable."
            )
        print(json.dumps({"source_hashes": carried}, indent=2))
    if not emitted:
        print(
            "\nno `source_hashes` blocks — baselines come from `locked` rows only. "
            "A draft row has no confirmation to record yet, so declaring the lock "
            "comes first: set `status: locked` on the row, then re-run this to get "
            "its block."
        )


#: Summary counters that mean "a human should look". Everything the report can
#: count is recorded, but only these decide whether an unattended run speaks up.
_NOTIFY_ON = (
    "gaps",
    "needs_reconciliation",
    "orphans",
    "orphans_needs_reconciliation",
    "drifted",
    "unchecked_sources",
    "refused_sources",
)

#: The three detection passes, in the order the report prints them.
_REPORT_PASSES = (
    ("coverage", "ingested pages no bank row grounds on", run_coverage),
    ("orphans", "rows whose grounding page left the live KB", run_orphans),
    ("drift", "locked rows whose grounding page changed", run_drift),
)


def _write_summary(args: argparse.Namespace, summary: dict) -> None:
    """Atomically write the --summary-json file (temp + os.replace).

    Mirrors write_ledger's commit step: the target is never truncated before the
    replacement is ready, so a crash or full-disk mid-write cannot corrupt an
    existing file.  Full fsync durability is not required for a re-derivable
    health signal (non-goal per design.md).
    """
    if not args.summary_json:
        return
    target = _output_target(pinned_output(args, "summary_json"), "the summary")
    tmp_name = ""
    handle = None
    committed = False
    try:
        fd, tmp_name = tempfile.mkstemp(
            dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp"
        )
        handle = os.fdopen(fd, "w", encoding="utf-8")
        json.dump(summary, handle, indent=2)
        handle.close()
        handle = None
        _preserve_access(target, tmp_name)
        _warn_if_multiply_linked(target, "the summary")
        os.replace(tmp_name, target)
        committed = True
    except OSError as exc:
        raise OperationalError(f"cannot write {args.summary_json}: {exc}") from exc
    finally:
        # See write_ledger: cleanup runs for every exception type, not only the
        # OSError the handler above converts.
        _close_quietly(handle)
        if not committed:
            _discard_quietly(tmp_name)


def bank_census(path: str) -> dict:
    """Bank composition, with a malformed row reported as an operational failure.

    `bank_status_counts` buckets rows by `anchor_type` and uses that value as a
    dictionary key, so the field's type has to be checked before the census, not
    after. Any non-text value is valid JSON inside a valid bank array — it passes
    `load_bank` untouched — and then breaks in one of two ways depending on what
    else is in the bank:

    - a list or object is **unhashable**, so the census itself dies;
    - a number is a perfectly good key, so the census succeeds and the run dies
      later at `sorted(census["anchor_type"].items())`, comparing `int` to `str`.

    Both are `TypeError`, and neither matches `run_report`'s handler or `main`'s:
    the process ends on a traceback **before** `--summary-json` is written, and
    the monitor goes on reading the previous run's healthy file — the exact
    failure this summary contract exists to close. A bank of nothing but numeric
    anchor types does not even crash: it sorts fine, `json.dump` stringifies the
    keys, and the census reports a bucket that is nowhere in the bank.

    So the field is validated up front rather than caught downstream. A bank the
    tool cannot census IS an operational failure, and raising it as one puts it
    on the failure-summary path with every other unreadable input.
    """
    bank = load_bank(path)
    for index, record in enumerate(bank):
        if not isinstance(record, dict):
            continue  # not a row the census reads a bucket from
        anchor = record.get("anchor_type")
        if anchor is None or isinstance(anchor, str):
            continue
        raise OperationalError(
            f"cannot census {path}: row {index} has an `anchor_type` of type "
            f"{type(anchor).__name__}; it must be text or absent"
        )
    return bank_status_counts(bank)


def run_report(args: argparse.Namespace) -> int:
    """Run all three passes as one unattended summary (design: cron contract).

    **Findings exit zero.** Gaps, orphans and drift are work to do, not a broken
    run — a nightly job that alerts on its own output trains its reader to
    ignore the alert, which is worse than no alert. Non-zero is reserved for a
    pass that *could not run*: an unreadable corpus, a missing source list, an
    inventory too incomplete to judge against.

    **Every pass runs even after an earlier one fails.** The passes are
    independent — the corpus, the live inventory and the source pages are three
    separate reads — so stopping at the first failure would throw away two
    working checks to report one broken one. Failures are collected and
    reprinted together at the end, because on a cron the summary line is the
    part a human actually reads.
    """
    summary: dict = {
        "census": None,
        "gaps": 0,
        "needs_reconciliation": 0,
        "orphans": 0,
        "orphans_needs_reconciliation": 0,
        "drifted": 0,
        "unchecked_sources": 0,
        "refused_sources": 0,
        "drift_check": "hash-only",
        "failed_passes": [],
        "notify": True,
    }
    try:
        census = bank_census(args.bank)
        summary["census"] = census
        summary["notify"] = False
    except OperationalError as exc:
        summary["failed_passes"] = [f"bank: {exc}"]
        summary["census"] = None
        summary["notify"] = True
        try:
            _write_summary(args, summary)
        except OperationalError as write_exc:
            # Two failures at once, and only one of them is actionable. Letting
            # the write error propagate from inside this handler would make it
            # the exception `main` prints, telling the operator the disk is full
            # and never that their bank is malformed. Both go to stderr; the
            # bank error stays the one that propagates.
            print(f"error: {write_exc}", file=sys.stderr)
        raise

    args.summary_sink = summary
    # Composition, printed once up front: the spec asks the reporting surface to
    # answer "how much of this bank has anyone actually vouched for?" from the
    # `status` field rather than by parsing `notes`. It is deliberately NOT a
    # notification trigger — a mostly-draft bank is a project status, not a
    # nightly alarm.
    print(
        f"bank: {census['total']} rows | {census['locked']} locked | "
        f"{census['draft']} draft"
    )
    if census["anchor_type"]:
        _print_group(
            "anchor_type distribution",
            [f"{k}: {v}" for k, v in sorted(census["anchor_type"].items())],
        )

    failures: List[str] = []
    for name, blurb, run in _REPORT_PASSES:
        print(f"\n{'=' * 70}\n== {name} — {blurb}\n{'=' * 70}")
        try:
            if run(args) != 0:
                # The pass already explained itself on stderr; record that it
                # did not complete so the exit code and the tail agree.
                failures.append(f"{name}: did not complete (see above)")
        except OperationalError as exc:
            print(f"error: {exc}", file=sys.stderr)
            failures.append(f"{name}: {exc}")
    summary["failed_passes"] = failures
    # Decided here rather than in the cron wrapper: which buckets deserve to
    # wake someone is a judgement about the domain, and it belongs where it can
    # be tested. `refused_sources` is included on purpose (see `run_drift`): a
    # host missing from `--allowed-hosts` is an omission, not a standing
    # decision, so an unchecked source notifies whether the page or the
    # allowlist is the reason it went unchecked.
    summary["notify"] = any(summary[key] > 0 for key in _NOTIFY_ON)
    # Written on every terminating path, including a bank-load failure: a
    # wrapper that finds no file (or a stale one) after a broken run cannot
    # distinguish it from a clean run — the exact confusion this file exists
    # to remove.
    _write_summary(args, summary)

    if failures:
        _print_group(
            "passes that could not run (this is what makes the run non-zero)",
            failures,
            stream=sys.stderr,
        )
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only coverage / orphan / drift detection for the RAGAS "
            "golden-set bank. Never writes the bank."
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
        nargs="+",
        required=True,
        metavar="HOST",
        help=(
            "REQUIRED — the hosts drift is authorized to contact (e.g. "
            "docs.rc.fas.harvard.edu slurm.schedmd.com). A `sources` value is "
            "data, and this pass dials it; anything not listed is refused "
            "unfetched, as are loopback/private/link-local and non-http(s) "
            "targets. There is no allow-everything default: this check sees only "
            "a hostname, so a public-looking name that resolves somewhere "
            "internal would pass it."
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

    report = sub.add_parser(
        "report",
        help="All three passes in one read-only run, for an unattended cron.",
    )
    add_bank(report)
    report_source = report.add_mutually_exclusive_group(required=True)
    report_source.add_argument("--corpus-json", help="JSON dump of `documents` rows.")
    report_source.add_argument("--pg-dsn", help="Postgres DSN for the live catalog.")
    report.add_argument(
        "--sources",
        required=True,
        help="Source list; `sitemap-` lines are expanded live.",
    )
    report.add_argument(
        "--allowed-hosts",
        nargs="+",
        required=True,
        metavar="HOST",
        help=(
            "REQUIRED — hosts the operator vouches for. Serves both passes that "
            "need one: the hosts `drift` may contact, and the extra hosts the "
            "sitemap may emit. There is no allow-everything default."
        ),
    )
    report.add_argument(
        "--min-pages",
        type=int,
        help=(
            "Sitemap completeness floor — match the deployment's "
            "sitemap.min_pages (FASRC: 150). Required when the source list "
            "contains a `sitemap-` line."
        ),
    )
    report.add_argument(
        "--max-pages", type=int, help="Sitemap cap — match sitemap.max_pages."
    )
    report.add_argument(
        "--ledger",
        help="Decision-ledger JSON, so declined pages stay suppressed nightly.",
    )
    report.add_argument(
        "--model",
        help=(
            "OPTIONAL `provider/model` for the advisory drift diff. Omitted by "
            "default: the hash tripwire is the finding, and an unattended job "
            "should not spend a provider call per drifted row without being asked."
        ),
    )
    report.add_argument(
        "--summary-json",
        metavar="PATH",
        help=(
            "Write per-pass finding counts and any failed passes here, as JSON. "
            "The exit code carries only ran/broke, and findings exit zero, so an "
            "unattended wrapper needs this to tell a clean run from one with "
            "work to do. Attempted on every path, including a failing run — but "
            "a write that itself fails leaves the previous file in place, so a "
            "monitor still has to check the exit code and the file's age. May "
            "not name a file this run reads."
        ),
    )
    # The passes are reused verbatim, so the flags they read but `report` does not
    # expose have to exist with their inert values. Named here rather than
    # defaulted inside each runner, so a reader can see in one place exactly which
    # interactive behaviours a cron run cannot reach.
    report.set_defaults(
        func=run_report,
        propose=None,
        decline=None,
        undecline=None,
        reason=None,
        source_type=None,
        parent=None,
        path_glob=None,
        data_path=None,
        count=3,
        # ON here, unlike the interactive `drift` where it is opt-in. The spec
        # asks for a drifted row to be flagged "with the re-fetched content for
        # review", and interactively that is a re-run away. Unattended there is
        # nobody to re-run it: this log is the whole artifact, and by the time
        # someone reads "3 drifted" the page may have moved again — so the
        # evidence has to be captured when it is detected. The wrapper's size
        # cap is what makes that safe to do nightly.
        show_text=True,
        print_hashes=False,
    )
    return parser


#: Every path flag, split by what the tool does with the file. Declared in ONE
#: table so a new flag has an obvious place to be classified, rather than a
#: second aliasing check growing somewhere else and going stale.
_OUTPUT_PATH_FLAGS = ("summary_json", "ledger")
_INPUT_PATH_FLAGS = ("bank", "corpus_json", "sources")


def _same_file(
    one: Path, other: Path, one_resolved: Path, other_resolved: Path
) -> bool:
    """Whether two paths name one file — by identity, not by spelling.

    Two checks, because neither subsumes the other:

    - `samefile` compares device + inode, so it sees a hard link and, more to the
      point, a second mount path to the same directory entry (a bind mount) that
      no amount of path resolution can normalise. It needs both files to exist.
    - resolved paths catch the case `samefile` cannot: an output that does not
      exist YET but is spelled through a symlinked directory, which is how a
      deployment reaching its data through `current -> releases/42` names one
      file two ways.

    Resolved with `_resolved_target`, i.e. `os.path.realpath`, and NOT with
    `Path.resolve()`. Before 3.13, `Path.resolve()` raises `RuntimeError` on a
    symlink loop; `main` catches `OperationalError`, so a self-referential input
    path ended the run on a traceback here — before `run_report`, and therefore
    before the `--summary-json` the operator is relying on could be refreshed.
    `realpath` returns the path instead, which lets the loop surface where it
    actually means something: the `ELOOP` raised when the file is *read*, already
    reported as a failed pass with a summary written. It also makes this guard
    and the writers agree, since they resolve the same paths the same way.

    The resolutions are handed IN rather than taken here, so the comparison and
    the pin that outlives it (`reject_aliased_outputs`) are literally one lookup
    instead of two that happen to agree. Resolving a second time to pin what was
    just compared would leave the refusal a check on a path nothing afterwards
    is guaranteed to use — the hole this whole pinning exists to close, reopened
    at its own seam.
    """
    try:
        if one.samefile(other):
            return True
    except OSError:  # one of them does not exist yet — fall through
        pass
    return one_resolved == other_resolved


def reject_aliased_outputs(args: argparse.Namespace) -> None:
    """Refuse a run whose output path is also one of its inputs.

    `os.replace` swaps the target's directory entry, so a `--summary-json`
    pointed at the bank does not "also write the summary" — it destroys the bank.
    The failure-summary path made that reachable on the one run where the input
    matters most: the bank is malformed, the operator is about to go repair it,
    and the failure summary lands on top of it. The same shape reaches the
    ledger, whose `--undecline` write replaces a `--corpus-json` dump — a list of
    objects that each carry a `url` validates as a decline ledger, so nothing
    downstream refuses it.

    Checked in `main` before the subcommand is dispatched, not at the write
    itself: this is the one refusal that has to happen while it is still true
    that nothing was read, printed or written. A tool that discovers the
    collision at its commit point has already spent the run.

    Each declared path is resolved ONCE here, and every output's resolution is
    **pinned** on `args` for the writers to use (`pinned_output`). Without that
    the refusal only describes the instant it ran: the writers looked their own
    paths up again afterwards, so retargeting a symlinked output mid-run walked
    straight past it.
    """
    declared = []
    for flag in _OUTPUT_PATH_FLAGS + _INPUT_PATH_FLAGS:
        value = getattr(args, flag, None)
        if value:
            declared.append((flag, Path(value), _resolved_target(str(value))))
    for flag, path, resolved in declared:
        if flag not in _OUTPUT_PATH_FLAGS:
            continue
        for other_flag, other_path, other_resolved in declared:
            if other_flag == flag or not _same_file(
                path, other_path, resolved, other_resolved
            ):
                continue
            raise OperationalError(
                f"--{flag.replace('_', '-')} and --{other_flag.replace('_', '-')} "
                f"are the same file ({path}). This run writes one and reads the "
                "other, so it would destroy its own input — give them separate "
                "paths."
            )
    args.pinned_outputs = {
        flag: resolved
        for flag, _path, resolved in declared
        if flag in _OUTPUT_PATH_FLAGS
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if not getattr(args, "func", None):
        print(
            "error: a subcommand is required " "(coverage | orphans | drift | report)",
            file=sys.stderr,
        )
        return 2
    try:
        reject_aliased_outputs(args)
        return args.func(args)
    except OperationalError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

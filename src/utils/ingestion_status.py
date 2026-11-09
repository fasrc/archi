"""Ingestion status tracking with lock-free reads.

The ingestion status dict is written by the ingestion thread and read by
Flask's request thread (``/api/ingestion/status``).  A dedicated lock
protects the status dict so the reader never blocks on the ingestion
mutual-exclusion lock, which is held for the entire ingest (22–64 min).

The status dict also carries a run identity: ``run_id``, ``started_at``, and
``finished_at``. A visible run starts when the state becomes ``running`` from
a non-``running`` state, and ends at the next terminal publish (``completed``
or ``error``). Runs that queue behind a running run share its ``run_id`` and
``started_at``; see ``_set_running`` and ``_finish``.

An upload through the uploader is tracked from the start of its collection by
``track_upload``: the collection takes no ingestion lock, and the vectorstore
update inside the block is counted as a run of its own, so ``completed`` is
never published between the two. A tracked upload holds no lock and publishes
no step of its own while another run is shown running; when the last
lock-taking run ends and only collections remain, ``_finish`` shows
``step="upload"`` for them. A collection that fails publishes ``error`` even
while the initial ingestion runs; the ingestion's next progress step then
starts a new run identity (``set_ingestion_status``), never a finished one,
and an ingestion that ends without such a step keeps the ``error``:
``completed`` never replaces a published failure (``_finish``).
"""

import logging
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterator, Optional

logger = logging.getLogger(__name__)


def build_ingestion_helpers(
    run_ingestion_fn: Callable[..., Any],
    ingestion_lock: threading.RLock,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
):
    """Build the ingestion lifecycle functions used by service_data_manager.

    Returns a dict with:
      - ``set_ingestion_status(state, *, step, error)``
      - ``set_ingestion_progress(done, total=None)``
      - ``get_ingestion_status() -> dict``
      - ``run_initial_ingestion_async()``
      - ``run_tracked(step, fn)``
      - ``run_source_refresh(name, func, update_vectorstore, set_source_status)``
      - ``run_upload_update(update_vectorstore)``
      - ``track_upload()`` — a context manager around an upload's collection
        and vectorstore update
      - ``ingestion_lock`` — the caller's ingestion mutual-exclusion lock

    ``now`` is a keyword-only seam, a callable returning an aware UTC
    ``datetime``, used to stamp ``started_at``/``finished_at``. It defaults to
    ``datetime.now(timezone.utc)`` and exists so tests can fix the clock.
    """
    _status_lock = threading.Lock()
    _status: Dict[str, object] = {
        "state": "pending",
        "step": None,
        "error": None,
        "progress": None,
        "run_id": None,
        "started_at": None,
        "finished_at": None,
    }

    def _new_run_identity() -> Dict[str, object]:
        return {
            "run_id": str(uuid.uuid4()),
            "started_at": now().isoformat(),
            "finished_at": None,
        }

    def set_ingestion_status(
        state: str, *, step: Optional[str] = None, error: Optional[str] = None
    ) -> None:
        """Publish a state and step from inside a run (the progress callback).

        ``running`` from a non-``running`` state starts a new run identity, as
        ``_set_running`` does: a tracked upload's collection can publish
        ``error`` while the initial ingestion still runs, and the ingestion's
        next step must not read as ``running`` with that run's ``finished_at``.
        ``progress`` is kept either way.
        """
        with _status_lock:
            update: Dict[str, object] = {"state": state, "step": step, "error": error}
            if state == "running" and _status["state"] != "running":
                update.update(_new_run_identity())
            _status.update(update)

    def set_ingestion_progress(done: int, total: Optional[int] = None) -> None:
        with _status_lock:
            _status["progress"] = {"done": done, "total": total}

    def get_ingestion_status() -> Dict[str, object]:
        with _status_lock:
            return dict(_status)

    # Runs that hold or wait for ingestion_lock, plus tracked uploads. Only the
    # lock owner publishes its step, and "completed" is published only when no
    # run is queued: a benchmark that sees "completed" must not have a corpus
    # change behind it.
    _inflight = 0
    # The tracked uploads among them. They hold no lock, so when they are all
    # that remains, no run will publish a step for them; see _finish.
    _collecting = 0

    def _set_running(step: str) -> None:
        """Publish running, assigning a fresh run identity if a new run started.

        A new run_id/started_at is assigned only when the previous state was
        not already "running"; a queued run that gets the lock after an
        earlier run's "running" keeps that run's identity.
        """
        update = {"state": "running", "step": step, "error": None, "progress": None}
        if _status["state"] != "running":
            update.update(_new_run_identity())
        _status.update(update)

    def _join(step: str, *, collecting: bool = False) -> None:
        """Count a run in, and publish its step if no other run is shown running.

        One _status_lock hold for both, so a run that joins next cannot publish
        between them and be masked. A terminal state is cleared here too,
        whatever the count of runs in flight: the previous owner can publish
        completed and still hold ingestion_lock, and a tracked upload that
        failed publishes error while other runs are in flight. A run that
        joins then is live work, so it is shown as a new run; the error is
        never shown over work that started after it. ``collecting`` marks a
        tracked upload, which holds no lock.
        """
        nonlocal _inflight, _collecting
        with _status_lock:
            _inflight += 1
            if collecting:
                _collecting += 1
            if _status["state"] != "running":
                _set_running(step)

    def _publish_running(step: str) -> None:
        with _status_lock:
            _set_running(step)

    def _finish(
        state: str,
        step: str,
        error: Optional[str] = None,
        *,
        collecting: bool = False,
    ) -> None:
        """Count a run out.

        A run that holds ingestion_lock calls it before the release; the
        upload tracker calls it without the lock, with ``collecting``. An
        ``error`` shown at a ``completed`` finish was published after this
        run's last ``running`` (a tracked collection that failed, or the
        update inside a tracker's block) and nothing cleared it since: it is
        kept, whatever the count in flight. ``completed`` never replaces a
        failure; a later failure does.

        A run that ends while only tracked uploads remain shows their step:
        no lock-taking run is left to publish one, and the finished run's
        embedding counter would otherwise stay frozen in the status, which a
        benchmark reads as a stall. A published ``error`` is left in place.
        """
        nonlocal _inflight, _collecting
        with _status_lock:
            _inflight -= 1
            if collecting:
                _collecting -= 1
            if state == "completed" and _status["state"] == "error":
                return
            if state == "error" or _inflight == 0:
                _status.update(
                    {
                        "state": state,
                        "step": step,
                        "error": error,
                        "finished_at": now().isoformat(),
                    }
                )
            elif _inflight == _collecting and _status["state"] == "running":
                _status.update({"step": "upload", "progress": None})

    def run_tracked(step: str, fn: Callable[[], Any]) -> Any:
        _join(step)
        with ingestion_lock:
            _publish_running(step)
            try:
                result = fn()
            except Exception as exc:
                _finish("error", "failed", str(exc))
                raise
            _finish("completed", "done")
            return result

    def _raise_if_failed(sync_status: Any) -> None:
        # A sync that could not add its documents returns "failed" rather than
        # raising; publishing "completed" after it would present a partial
        # corpus as a finished one.
        if sync_status == "failed":
            raise RuntimeError("vectorstore update failed: documents not added")

    def _update_or_raise(update_vectorstore: Callable[..., Any]) -> None:
        _raise_if_failed(update_vectorstore(force=True))

    def run_upload_update(update_vectorstore: Callable[..., Any]) -> None:
        run_tracked("upload", lambda: _update_or_raise(update_vectorstore))

    @contextmanager
    def track_upload() -> Iterator[None]:
        """Report an upload as running from the start of its collection.

        The block takes no ingestion_lock, so a collection never waits behind
        an ingest. It counts the upload in before the block and out after it;
        the vectorstore update inside the block joins and finishes as a run of
        its own, so the counter keeps the status at ``running`` between the
        two. A block that raises publishes ``error`` with the message. A block
        that ends normally publishes ``completed`` when no other run is in
        flight, unless an ``error`` is shown (its update failed). While a
        lock-taking run is shown running, the block publishes no step; when
        that run ends first, ``_finish`` shows ``upload`` for the collection.
        """
        _join("upload", collecting=True)
        try:
            yield
        except Exception as exc:
            _finish("error", "failed", str(exc), collecting=True)
            raise
        _finish("completed", "done", collecting=True)

    def run_source_refresh(
        name: str,
        func: Callable[[], None],
        update_vectorstore: Callable[..., Any],
        set_source_status: Callable[..., None],
    ) -> None:
        def body() -> None:
            logger.info("Running ingestion task: %s", name)
            try:
                set_source_status(name, state="running")
                func()
                logger.info("Updating vectorstore after scheduled task: %s", name)
                _update_or_raise(update_vectorstore)
                set_source_status(
                    name,
                    state="idle",
                    last_run=datetime.now(timezone.utc).isoformat(),
                )
            except Exception:
                try:
                    set_source_status(name, state="error")
                except Exception:
                    logger.exception("Failed to record error status for %s", name)
                raise

        run_tracked(f"scheduled:{name}", body)

    def run_initial_ingestion_async() -> None:
        _join("initializing")
        with ingestion_lock:
            _publish_running("initializing")
            try:
                _raise_if_failed(
                    run_ingestion_fn(
                        progress_callback=lambda step: set_ingestion_status(
                            "running", step=step
                        ),
                        embedding_progress=set_ingestion_progress,
                    )
                )
            except Exception as exc:
                logger.exception("Initial ingestion failed")
                _finish("error", "failed", str(exc))
                return
            _finish("completed", "done")

    return {
        "set_ingestion_status": set_ingestion_status,
        "set_ingestion_progress": set_ingestion_progress,
        "get_ingestion_status": get_ingestion_status,
        "run_initial_ingestion_async": run_initial_ingestion_async,
        "run_tracked": run_tracked,
        "run_source_refresh": run_source_refresh,
        "run_upload_update": run_upload_update,
        "track_upload": track_upload,
        "ingestion_lock": ingestion_lock,
    }

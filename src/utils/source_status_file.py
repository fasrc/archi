"""One owner for the per-source status file ``ingestion_status.json`` (#700).

``GET /api/schedules`` reads this file. Three writers change it: the data
manager's ``set_source_status`` (scheduled refreshes), its ``reload_schedules``
and start-up seed, and the uploader's ``FlaskAppWrapper._update_source_status``.
All of them run in the data-manager process (``service_data_manager.main``
builds the uploader), so one process-wide lock is enough. That is true while
``services.data_manager.flask_debug_mode`` is false (the default): in debug mode
Werkzeug's reloader runs ``main()`` again in a second process, and this lock
does not cover writes from the other process.

Each write holds ``_LOCK`` around read -> change -> write, so two writes that
overlap cannot lose an update. The write is atomic: the new content goes to a
temporary file in the same directory, then ``os.replace`` puts it in place, so a
reader sees the old file or the new file, never a part of one. Readers do not
take the lock. The lock is a leaf: code that holds it takes no other lock of
this service (a log call takes only the logging handler's own lock).
"""

import json
import os
import threading
from pathlib import Path
from typing import Callable, Dict, Mapping, Optional

from src.utils.logging import get_logger

logger = get_logger(__name__)

SourceStatus = Dict[str, Dict[str, str]]

_LOCK = threading.Lock()


def load_source_status(path: Path) -> SourceStatus:
    """Return the file's content, or ``{}`` when it is absent or unreadable.

    An absent file is not logged. An unreadable file, or content that is not a
    JSON object, logs a WARNING. A write after it replaces the whole file.
    """
    path = Path(path)
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except Exception as exc:
        logger.warning("Failed to read source status file %s: %s", path, exc)
        return {}
    if not isinstance(data, dict):
        logger.warning("Source status file %s is not a JSON object; ignored", path)
        return {}
    return data


def _write_atomic(path: Path, data: SourceStatus) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w") as handle:
            json.dump(data, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _mutate(path: Path, change: Callable[[SourceStatus], None]) -> None:
    path = Path(path)
    with _LOCK:
        data = load_source_status(path)
        change(data)
        _write_atomic(path, data)


def update_source_status(
    path: Path,
    source: str,
    *,
    state: Optional[str] = None,
    last_run: Optional[str] = None,
    schedule: Optional[str] = None,
) -> None:
    """Change the given fields of one source's entry; keep every other field.

    An empty ``schedule`` removes the entry's schedule. A write error raises.
    """

    def change(data: SourceStatus) -> None:
        entry = data.get(source)
        if not isinstance(entry, dict):
            entry = {}
        if state is not None:
            entry["state"] = state
        if last_run is not None:
            entry["last_run"] = last_run
        if schedule is not None:
            if schedule:
                entry["schedule"] = schedule
            else:
                entry.pop("schedule", None)
        data[source] = entry

    _mutate(path, change)


def seed_source_schedules(path: Path, schedules: Mapping[str, str]) -> None:
    """Give each scheduled source an entry; keep the values an entry already has.

    A new entry gets the schedule and ``state == "idle"``.
    """

    def change(data: SourceStatus) -> None:
        for source, schedule in schedules.items():
            if not schedule:
                continue
            entry = data.get(source)
            if not isinstance(entry, dict):
                entry = {}
            entry.setdefault("schedule", schedule)
            entry.setdefault("state", "idle")
            data[source] = entry

    _mutate(path, change)


def set_source_schedules(path: Path, schedules: Mapping[str, str]) -> None:
    """Set the schedule of each source that already has an entry."""

    def change(data: SourceStatus) -> None:
        for source, schedule in schedules.items():
            entry = data.get(source)
            if isinstance(entry, dict):
                entry["schedule"] = schedule

    _mutate(path, change)

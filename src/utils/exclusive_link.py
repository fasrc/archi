"""Create a file that never replaces an existing one (judge offload design D10).

Standard library only, with no import from ``src``: the judge queue of #732 runs
on a login node and loads this file by path.
"""

from __future__ import annotations

import os
import secrets
import socket
from typing import Union


def link_exclusive(path: Union[str, os.PathLike], data: bytes) -> bool:
    """Create *path* with the bytes *data*; never replace an existing *path*.

    The bytes go to a unique temporary file in the same folder, are fsynced,
    and only then get the name through ``os.link``, which fails when the name
    exists. So the name never exists without its full content, and of several
    writers of one name exactly one wins.

    Returns True when this call created *path*. Returns False when another
    writer owns it; the caller compares bytes if it needs to.
    """
    target = os.fspath(path)
    folder = os.path.dirname(target) or "."
    tmp = os.path.join(
        folder, f".tmp.{socket.gethostname()}.{os.getpid()}.{secrets.token_hex(8)}"
    )
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(tmp, target)
        except FileExistsError:
            # An NFS client can resend a link that succeeded and then report
            # EEXIST. The temporary file then has a second name: the target.
            return os.stat(tmp).st_nlink == 2
        return True
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass

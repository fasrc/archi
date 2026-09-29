"""Server-side character ceiling for `api_catalog_document` (issue #260).

`request.args.get("max_chars", default=4000, type=int)` truncated only when
`max_chars` was truthy, so `0` and a negative value both returned the whole
document instead of a bounded one. `clamp_document_chars` reuses the tested
client-side rule (`resolve_requested_chars`) so `0` means "the ceiling",
never "no limit".
"""

from typing import Any

MAX_CATALOG_DOCUMENT_CHARS = 6000
DEFAULT_CATALOG_DOCUMENT_CHARS = 4000


def clamp_document_chars(requested: Any) -> int:
    """Resolve a caller-supplied ``max_chars`` against the 6000 ceiling.

    ``resolve_requested_chars`` is imported lazily on purpose: its parent
    package ``src.archi.pipelines`` imports every agent class and the MCP
    adapters at import time. Importing it at module load would pull all of
    that into the uploader app's startup, which otherwise loads none of it.
    """
    if requested is None:
        return DEFAULT_CATALOG_DOCUMENT_CHARS
    from src.archi.pipelines.agents.tools.result_limits import resolve_requested_chars

    return resolve_requested_chars(requested, MAX_CATALOG_DOCUMENT_CHARS)

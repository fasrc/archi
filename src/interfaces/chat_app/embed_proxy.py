"""Chat-side proxy for the data manager's tracked embed update (issue #652)."""

from typing import Optional, Tuple

import requests

from src.utils.logging import get_logger

logger = get_logger(__name__)

# The data manager answers only when its update ends, and the update can wait
# for the ingestion lock behind an ingest that runs for up to about an hour.
EMBED_CONNECT_TIMEOUT_SECONDS = 10
EMBED_READ_TIMEOUT_SECONDS = 3600

_REDIRECT_CODES = (301, 302, 303, 307, 308)


def request_embed(data_manager_url: str, headers: dict) -> Optional[Tuple[dict, int]]:
    """Ask the data manager to run its tracked embed update.

    Returns None on success, else the (body, status) the chat app answers with.
    """
    try:
        resp = requests.post(
            f"{data_manager_url}/document_index/embed",
            headers=headers,
            timeout=(EMBED_CONNECT_TIMEOUT_SECONDS, EMBED_READ_TIMEOUT_SECONDS),
            allow_redirects=False,
        )
    except requests.exceptions.ConnectionError:
        logger.error("Data manager service unavailable")
        return {"error": "data_manager_unavailable"}, 503
    except requests.exceptions.Timeout:
        logger.error(
            "Data manager did not answer the embed request in time; "
            "its GET /api/ingestion/status shows whether the update still runs"
        )
        return (
            {
                "error": "The data manager did not answer in time. The update can "
                "still be running: reload this page later to see the document status."
            },
            504,
        )

    if resp.is_redirect or resp.status_code in _REDIRECT_CODES:
        logger.error("Data-manager rejected embed (auth redirect)")
        return {"error": "Data manager authentication failed"}, 502

    try:
        body = resp.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        return {"error": f"Data manager error (HTTP {resp.status_code})"}, 502

    if 200 <= resp.status_code < 300:
        if body.get("status") == "ok":
            return None
        return {"error": f"Data manager error (HTTP {resp.status_code})"}, 502

    message = body.get("detail") or body.get("error") or "embed_failed"
    return {"error": message}, 500

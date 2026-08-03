from datetime import datetime, timezone
from typing import Any


class InvalidLastMessage(ValueError):
    pass


class InvalidClientTimestamp(ValueError):
    pass


def check_client_sent_msg_ts(seconds: float) -> None:
    """Refuse a supplied client send time that cannot be represented as a datetime.

    ``client_sent_msg_ts`` used to be screened out by the unconditional deadline check:
    any absurd value made ``server_received - client_sent`` exceed the timeout and the
    request was refused with 408 before the pipeline ran. Now that the check requires a
    truthy ``client_timeout``, a supplied-but-unrepresentable timestamp reaches
    ``datetime.fromtimestamp`` at persistence time instead -- after generation has been
    paid for -- where it raises ``OSError`` (beyond the platform's ``time_t``),
    ``OverflowError``, or ``ValueError`` (outside years 1-9999) depending on the value and
    the platform. On the streaming route that lands mid-stream, so the caller has already
    been handed HTTP 200.

    A falsey value means "not supplied" and is left alone -- that is the documented
    optional case, not a bad one.

    The check is the conversion itself rather than a hardcoded range, so it cannot
    disagree with the two call sites it is protecting about where the boundary is.
    """
    if not seconds:
        return
    try:
        datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OSError, OverflowError, ValueError) as exc:
        raise InvalidClientTimestamp(
            "client_sent_msg_ts is not a representable time; send milliseconds "
            "since the Unix epoch, generated at send time"
        ) from exc


def parse_last_message(value: Any) -> tuple[str, str]:
    """Validate and unpack the first element of a last_message payload.

    Accepts a non-empty list or tuple whose first element is itself a list or
    tuple of exactly two strings. Returns (sender, content). Raises
    InvalidLastMessage for any other shape.
    """
    _SHAPE_HINT = '[["User", "hello"]]'
    _MSG = (
        f"last_message must be a list containing a [sender, message] pair of "
        f"two strings, e.g. {_SHAPE_HINT}"
    )

    if not isinstance(value, (list, tuple)) or len(value) == 0:
        raise InvalidLastMessage(_MSG)

    first = value[0]

    # Strings are sequences but are not a valid pair container — reject them
    # explicitly, because "AI" is a two-character sequence that would otherwise
    # unpack to ("A", "I") rather than raising (the original issue #167 bug).
    if not isinstance(first, (list, tuple)):
        raise InvalidLastMessage(_MSG)

    if len(first) != 2:
        raise InvalidLastMessage(_MSG)

    sender, content = first
    if not isinstance(sender, str) or not isinstance(content, str):
        raise InvalidLastMessage(_MSG)

    return sender, content

from datetime import datetime, timezone
from typing import Any


class InvalidLastMessage(ValueError):
    pass


class InvalidClientTiming(ValueError):
    pass


# Both timing fields arrive as milliseconds and are divided by 1000. That division is part
# of the untrusted-input surface, not a formality: `9…9 / 1000` on a 1001-digit JSON
# integer raises OverflowError, and `"600000" / 1000` on a quoted number raises TypeError.
# Normalizing and validating therefore have to happen in the same guarded step -- a range
# check placed after the division never runs for those inputs.
_MS_ERRORS = (OverflowError, TypeError)


def _milliseconds_to_seconds(value: Any, field: str) -> float:
    if not value:
        # Falsey means "not supplied" -- the documented optional case, not a bad one.
        return 0
    try:
        return value / 1000
    except _MS_ERRORS as exc:
        raise InvalidClientTiming(
            f"{field} must be a number of milliseconds; got a value that cannot be "
            f"converted ({type(value).__name__})"
        ) from exc


def parse_client_sent_msg_ts(value: Any) -> float:
    """Normalize a client_sent_msg_ts payload value to seconds, or refuse it.

    The deadline check used to screen absurd values out incidentally: any of them made
    ``server_received - client_sent`` exceed the timeout, so the request was refused with
    408 before the pipeline ran. Now that the check requires a truthy ``client_timeout``,
    an unrepresentable timestamp instead reaches ``datetime.fromtimestamp`` at persistence
    time -- after generation has been paid for -- where it raises ``OSError`` (beyond the
    platform's ``time_t``), ``OverflowError``, or ``ValueError`` (outside years 1-9999)
    depending on the value and the platform. On the streaming route that lands inside the
    generator, so the caller has already been handed HTTP 200.

    The range check is the conversion itself rather than a hardcoded bound, so it cannot
    disagree with the two persistence sites it protects about where the boundary is.
    """
    seconds = _milliseconds_to_seconds(value, "client_sent_msg_ts")
    if not seconds:
        return seconds
    try:
        datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OSError, OverflowError, ValueError) as exc:
        raise InvalidClientTiming(
            "client_sent_msg_ts is not a representable time; send milliseconds "
            "since the Unix epoch, generated at send time"
        ) from exc
    return seconds


def parse_client_timeout(value: Any) -> float:
    """Normalize a client_timeout payload value to seconds, or refuse it.

    No range check: the value is only ever compared against an elapsed interval, so any
    magnitude a float can hold is meaningful (a huge one simply means "no deadline in
    practice"). Only the division can fail.
    """
    return _milliseconds_to_seconds(value, "client_timeout")


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

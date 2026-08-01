from typing import Any


class InvalidLastMessage(ValueError):
    pass


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

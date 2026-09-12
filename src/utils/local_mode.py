from typing import Dict, Optional

MODE_OLLAMA = "ollama"
MODE_OPENAI_COMPAT = "openai_compat"

LOCAL_MODES = {MODE_OLLAMA, MODE_OPENAI_COMPAT}


def canonical_local_mode(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(
            f"local_mode must be a string, got {value!r}; "
            f"valid values: {MODE_OLLAMA!r}, {MODE_OPENAI_COMPAT!r}"
        )
    normalized = value.strip().lower()
    if normalized not in LOCAL_MODES:
        raise ValueError(
            f"Unrecognized local_mode {value!r}; "
            f"valid values: {MODE_OLLAMA!r}, {MODE_OPENAI_COMPAT!r}"
        )
    return normalized


def apply_local_mode(
    extra: Dict, raw: Optional[str], *, overwrite: bool = True
) -> None:
    canonical = canonical_local_mode(raw)
    if canonical is None:
        return
    if not overwrite and "local_mode" in extra:
        return
    extra["local_mode"] = canonical

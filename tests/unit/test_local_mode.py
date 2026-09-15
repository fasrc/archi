import pytest

from src.utils.local_mode import (
    LOCAL_MODES,
    MODE_OLLAMA,
    MODE_OPENAI_COMPAT,
    apply_local_mode,
    canonical_local_mode,
)

# --- canonical_local_mode ---


@pytest.mark.parametrize(
    "value,expected",
    [
        ("openai_compat", "openai_compat"),
        ("OpenAI_Compat", "openai_compat"),
        ("OPENAI_COMPAT", "openai_compat"),
        (" openai_compat ", "openai_compat"),
        ("ollama", "ollama"),
        ("Ollama", "ollama"),
        ("  OLLAMA  ", "ollama"),
        (None, None),
    ],
)
def test_canonical_local_mode_valid(value, expected):
    assert canonical_local_mode(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "openai-compat",
        "vllm",
        "",
        "   ",
        5,
    ],
)
def test_canonical_local_mode_invalid_raises(value):
    with pytest.raises(ValueError):
        canonical_local_mode(value)


def test_canonical_local_mode_error_message_names_rejected_and_valid():
    with pytest.raises(ValueError, match="openai_compat") as exc_info:
        canonical_local_mode("vllm")
    msg = str(exc_info.value)
    assert "vllm" in msg
    assert "ollama" in msg
    assert "openai_compat" in msg


def test_canonical_local_mode_idempotent():
    for mode in LOCAL_MODES:
        assert canonical_local_mode(canonical_local_mode(mode)) == mode


# --- apply_local_mode ---


def test_apply_local_mode_recognized_writes_canonical():
    extra = {}
    apply_local_mode(extra, "OpenAI_Compat")
    assert extra["local_mode"] == "openai_compat"


def test_apply_local_mode_none_writes_nothing():
    extra = {}
    apply_local_mode(extra, None)
    assert extra == {}


def test_apply_local_mode_unrecognized_raises_before_touching_extra():
    extra = {}
    with pytest.raises(ValueError):
        apply_local_mode(extra, "vllm")
    assert extra == {}


def test_apply_local_mode_overwrite_false_existing_key_wins():
    extra = {"local_mode": "ollama"}
    apply_local_mode(extra, "openai_compat", overwrite=False)
    assert extra["local_mode"] == "ollama"


def test_apply_local_mode_overwrite_false_no_key_writes_canonical():
    extra = {}
    apply_local_mode(extra, "openai_compat", overwrite=False)
    assert extra["local_mode"] == "openai_compat"


def test_apply_local_mode_overwrite_true_replaces_existing():
    extra = {"local_mode": "ollama"}
    apply_local_mode(extra, "openai_compat", overwrite=True)
    assert extra["local_mode"] == "openai_compat"


# --- constants ---


def test_mode_constants_are_in_local_modes():
    assert MODE_OLLAMA in LOCAL_MODES
    assert MODE_OPENAI_COMPAT in LOCAL_MODES

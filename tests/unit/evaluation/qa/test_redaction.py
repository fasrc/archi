"""Tests for src.evaluation.qa.redaction — key rule, value redaction, idempotence."""

import copy
import datetime

import pytest
import yaml

from src.evaluation.qa.redaction import REDACTED, is_secret_key, redact_agent_config

# ---------------------------------------------------------------------------
# D2 — must-be-secret table
# ---------------------------------------------------------------------------

_MUST_BE_SECRET = [
    "api_key",
    "apiKey",
    "APIKey",
    "API_KEY",
    "x-api-key",
    "X-Api-Key",
    "apikey",
    "key",
    "keys",
    "provider_api_keys",
    "secret",
    "secrets",
    "client_secret",
    "clientSecret",
    "secret_key",
    "secretkey",
    "password",
    "PG_PASSWORD",
    "POSTGRES_PASSWORD",
    "PGPASSWORD",
    "admin_password",
    "db_passwd",
    "token",
    "access_token",
    "accessToken",
    "accesstoken",
    "auth_token",
    "authtoken",
    "hf_token",
    "HUGGING_FACE_HUB_TOKEN",
    "Authorization",
    "proxy-authorization",
    "cookie",
    "Set-Cookie",
    "session",
    "credentials",
    "encryption_key",
    "private_key",
    "csrf_token",
    "dsn",
    "bearer",
    "privatekey",
    "PRIVATEKEY",
    "accesskey",
    "sessionkey",
    "signingkey",
    "encryptionkey",
    "masterkey",
    "authtokens",
    "accesstokens",
    "refreshtokens",
    "monkey",
    "access_tokens",
    "accessTokens",
    "refresh_tokens",
    "apiTokens",
    "auth_tokens",
    "bearer_tokens",
    "passphrases",
    "db_passphrases",
    "dbPassphrases",
    "dbpassphrase",
    "dbpassphrases",
]

# ---------------------------------------------------------------------------
# D2 — must-not-be-secret table
# ---------------------------------------------------------------------------

_MUST_NOT_BE_SECRET = [
    "max_tokens",
    "preferred_max_tokens",
    "prompt_tokens",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "per_result_tokens",
    "additional_special_tokens",
    "tokenizer",
    "keywords",
    "keyword",
    "auth",
    "auth_roles",
    "auth_method",
    "authorize_url",
    "agent_class",
    "default_provider",
    "default_model",
    "base_url",
    "embedding_name",
    "collection_name",
    "primaryauthors",
    "coauthors",
    "tokens",
    "maxtokens",
]


@pytest.mark.parametrize("name", _MUST_BE_SECRET)
def test_secret_key_names_are_redacted(name):
    assert is_secret_key(name) is True


@pytest.mark.parametrize("name", _MUST_NOT_BE_SECRET)
def test_non_secret_key_names_are_kept(name):
    assert is_secret_key(name) is False


# ---------------------------------------------------------------------------
# D3 — nested fixture
# ---------------------------------------------------------------------------


def _fixture():
    return {
        "api_key": "s1",
        "max_tokens": 4096,
        "extra_kwargs": {
            "headers": {
                "Authorization": "Bearer s2",
            }
        },
        "secrets": {
            "PG_PASSWORD": "s3",
            "nested": ["s4", {"x": "s5"}],
        },
        "headers_list": [
            {"name": "X-Api-Key", "value": "s6"},
            {"name": "Accept", "value": "json"},
        ],
        "database_url": "postgresql://u:s7@db:5432/x",
        "dsn": "postgresql://u:s8@db/x",
        "api_token": None,
        "password": "",
        "session": True,
        1: "one",
    }


def test_redaction_replaces_values_and_keeps_structure():
    fixture = _fixture()
    original = copy.deepcopy(fixture)
    out = redact_agent_config(fixture)

    # secret key → scalar str: redacted
    assert out["api_key"] == REDACTED
    # non-secret key, int → unchanged
    assert out["max_tokens"] == 4096
    # nested mapping under secret key → secret mode: all non-empty str redacted
    assert out["extra_kwargs"]["headers"]["Authorization"] == REDACTED
    # secrets block: secret key → secret mode
    assert out["secrets"]["PG_PASSWORD"] == REDACTED
    assert out["secrets"]["nested"][0] == REDACTED
    assert out["secrets"]["nested"][1]["x"] == REDACTED
    # header-list form: name is a secret key → value redacted
    assert out["headers_list"][0]["value"] == REDACTED
    assert out["headers_list"][0]["name"] == "X-Api-Key"
    # non-secret header name → value unchanged
    assert out["headers_list"][1]["value"] == "json"
    # URL in non-secret key: password replaced, not REDACTED
    assert out["database_url"] == "postgresql://u:redacted@db:5432/x"
    # dsn is a secret key → whole value is REDACTED
    assert out["dsn"] == REDACTED
    # None → unchanged
    assert out["api_token"] is None
    # empty string → unchanged
    assert out["password"] == ""
    # bool → unchanged (even under secret key "session")
    assert out["session"] is True
    # non-string key stays
    assert out[1] == "one"

    # key order preserved at every mapping level
    assert list(out) == list(fixture)
    assert list(out["extra_kwargs"]["headers"]) == list(
        fixture["extra_kwargs"]["headers"]
    )
    assert list(out["secrets"]) == list(fixture["secrets"])

    # input not mutated
    assert fixture == original


# ---------------------------------------------------------------------------
# D3 — URL rows
# ---------------------------------------------------------------------------

_URL_ROWS = [
    ("postgresql://u:s7@db:5432/x", "postgresql://u:redacted@db:5432/x"),
    ("dsn=postgresql://u:pw@h/x", "dsn=postgresql://u:redacted@h/x"),
    (
        "https://u:p1@h one https://v:p2@h",
        "https://u:redacted@h one https://v:redacted@h",
    ),
    ("postgresql://u:p@ss@db/x", "postgresql://u:redacted@db/x"),
    ("https://user@h/x", "https://user@h/x"),
    ("https://h/x", "https://h/x"),
    # Only the password changes; the host text is kept byte for byte, so an
    # unparseable host no longer turns the whole string into REDACTED.
    ("https://u:pw@[::1", "https://u:redacted@[::1"),
    ("http://u:p@[::1]:8080/x", "http://u:redacted@[::1]:8080/x"),
    ("http://u:p@h:99999/", "http://u:redacted@h:99999/"),
    (
        "https://u:p@h/?next=https://v:q@other/",
        "https://u:redacted@h/?next=https://v:redacted@other/",
    ),
    ("https://host:8080/p?email=a@b.com", "https://host:8080/p?email=a@b.com"),
]


@pytest.mark.parametrize("url_in,url_out", _URL_ROWS)
def test_url_password_is_redacted(url_in, url_out):
    out = redact_agent_config({"endpoint": url_in})
    assert out["endpoint"] == url_out


@pytest.mark.parametrize("url_in,url_out", _URL_ROWS)
def test_url_redaction_is_idempotent(url_in, url_out):
    once = redact_agent_config({"endpoint": url_in})
    assert redact_agent_config(once) == once


@pytest.mark.parametrize(
    "value",
    [["Bearer SECRET"], {"token": "Bearer SECRET"}, [{"v": "Bearer SECRET"}]],
)
def test_header_list_container_value_is_redacted(value):
    out = redact_agent_config({"headers": [{"name": "Authorization", "value": value}]})
    assert "SECRET" not in repr(out)


@pytest.mark.parametrize(
    "value", [datetime.date(2026, 9, 30), b"xx", datetime.datetime(2026, 9, 30)]
)
def test_other_scalar_types_under_a_secret_key_are_redacted(value):
    out = redact_agent_config(
        {
            "password": value,
            "secrets": {"inner": value},
            "headers": [{"name": "X-Api-Key", "value": value}],
        }
    )
    assert out["password"] == REDACTED
    assert out["secrets"]["inner"] == REDACTED
    assert out["headers"][0]["value"] == REDACTED


# ---------------------------------------------------------------------------
# Idempotence
# ---------------------------------------------------------------------------


def test_redaction_is_idempotent():
    fixture = _fixture()
    once = redact_agent_config(fixture)
    twice = redact_agent_config(once)
    assert twice == once
    assert yaml.safe_dump(twice, sort_keys=False, allow_unicode=True) == yaml.safe_dump(
        once, sort_keys=False, allow_unicode=True
    )


# ---------------------------------------------------------------------------
# D5 — drift guard against config_fingerprint._SENSITIVE_HINTS
# ---------------------------------------------------------------------------


def test_rule_covers_chat_app_sensitive_hints():
    from src.interfaces.chat_app.config_fingerprint import _SENSITIVE_HINTS

    assert _SENSITIVE_HINTS, "_SENSITIVE_HINTS must be non-empty"
    for hint in _SENSITIVE_HINTS:
        assert is_secret_key(hint), f"is_secret_key({hint!r}) should be True"
        assert is_secret_key(
            f"api_{hint}"
        ), f"is_secret_key('api_{hint}') should be True"
        assert is_secret_key(
            f"private{hint}"
        ), f"is_secret_key('private{hint}') should be True"
        assert is_secret_key(
            f"access{hint}"
        ), f"is_secret_key('access{hint}') should be True"

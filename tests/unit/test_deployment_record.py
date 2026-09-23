"""Unit tests for the deployment-provenance record.

``ensure_config`` already computes the config HEAD, the pin, the match verdict
and the dirty paths, then only logs them. These tests pin the contract by which
those values cross into the container as environment variables and become a
database row the running deployment can read.
"""

import pytest

from src.utils.deployment_record import (
    DEPLOYMENT_ENV_KEYS,
    PIN_STATE_LIVE_EDITED,
    PIN_STATE_MATCHED,
    PIN_STATE_UNKNOWN,
    build_deployment_record,
    is_live_edited,
    pin_state,
    record_deployment,
    write_deployment_record,
)


def _env(**overrides):
    base = {
        "ARCHI_CONFIG_REF": "deploy-pin-2026-09e",
        "ARCHI_CONFIG_SHA": "48022ed74a1f5eed183268d0f82fd7c6d646f9b5",
        "ARCHI_CONFIG_HEAD": "48022ed74a1f5eed183268d0f82fd7c6d646f9b5",
        "ARCHI_CONFIG_PIN_MATCHED": "yes",
        "ARCHI_CONFIG_DIRTY_PATHS": "",
        "APP_VERSION": "v2026.08.0-32-g8667940d",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# build_deployment_record
# ---------------------------------------------------------------------------


def test_record_reads_every_value_from_the_environment():
    record = build_deployment_record(_env())

    assert record == {
        "config_ref": "deploy-pin-2026-09e",
        "config_sha": "48022ed74a1f5eed183268d0f82fd7c6d646f9b5",
        "config_head": "48022ed74a1f5eed183268d0f82fd7c6d646f9b5",
        "pin_matched": True,
        "dirty_paths": "",
        "app_version": "v2026.08.0-32-g8667940d",
    }


def test_record_declares_the_environment_keys_it_reads():
    assert set(DEPLOYMENT_ENV_KEYS) == {
        "ARCHI_CONFIG_REF",
        "ARCHI_CONFIG_SHA",
        "ARCHI_CONFIG_HEAD",
        "ARCHI_CONFIG_PIN_MATCHED",
        "ARCHI_CONFIG_DIRTY_PATHS",
        "APP_VERSION",
    }


@pytest.mark.parametrize(
    "raw,expected",
    [("yes", True), ("no", False), ("YES", True), ("No", False)],
)
def test_pin_matched_parses_the_shells_yes_or_no(raw, expected):
    """``ensure_config`` sets match=yes/no, not true/false."""
    record = build_deployment_record(_env(ARCHI_CONFIG_PIN_MATCHED=raw))
    assert record["pin_matched"] is expected


@pytest.mark.parametrize("raw", ["", "   ", "maybe"])
def test_pin_matched_is_unknown_when_it_is_not_yes_or_no(raw):
    """An unset or unparseable verdict must not silently read as 'matched'."""
    record = build_deployment_record(_env(ARCHI_CONFIG_PIN_MATCHED=raw))
    assert record["pin_matched"] is None


def test_pin_matched_is_unknown_when_absent():
    env = _env()
    del env["ARCHI_CONFIG_PIN_MATCHED"]
    assert build_deployment_record(env)["pin_matched"] is None


def test_absent_values_become_none_rather_than_the_string_unknown():
    assert build_deployment_record({}) == {
        "config_ref": None,
        "config_sha": None,
        "config_head": None,
        "pin_matched": None,
        "dirty_paths": None,
        "app_version": None,
    }


@pytest.mark.parametrize("raw", ["", "   ", "unknown"])
def test_app_version_placeholders_become_none(raw):
    """APP_VERSION defaults to the literal 'unknown' when no build arg is passed.

    Storing that string would put the word 'unknown' in front of an operator as
    though it were a version.
    """
    assert build_deployment_record(_env(APP_VERSION=raw))["app_version"] is None


def test_values_are_stripped_of_surrounding_whitespace():
    record = build_deployment_record(_env(ARCHI_CONFIG_REF="  pin-1  "))
    assert record["config_ref"] == "pin-1"


# ---------------------------------------------------------------------------
# is_live_edited
# ---------------------------------------------------------------------------


def test_clean_on_pin_deploy_is_not_live_edited():
    assert is_live_edited(build_deployment_record(_env())) is False


def test_tracked_edits_on_the_pin_are_live_edited():
    """The subtle case: HEAD matches the pin, but the tree carries edits.

    ensure_config permits this deploy, so the pin alone would misrepresent it.
    """
    record = build_deployment_record(
        _env(ARCHI_CONFIG_DIRTY_PATHS="M\tlists/sources.list")
    )
    assert is_live_edited(record) is True


def test_an_off_pin_deploy_is_live_edited():
    record = build_deployment_record(_env(ARCHI_CONFIG_PIN_MATCHED="no"))
    assert is_live_edited(record) is True


def test_an_unknown_verdict_is_not_a_live_edit():
    """Unknown provenance is its own state, not an accusation.

    A hand-run `archi create` records no verdict. Calling that a live edit
    would accuse a possibly-clean deployment of an edit nothing observed.
    """
    record = build_deployment_record(_env(ARCHI_CONFIG_PIN_MATCHED=""))
    assert is_live_edited(record) is False
    assert pin_state(record) == PIN_STATE_UNKNOWN


def test_an_unknown_verdict_is_not_reported_as_matched_either():
    record = build_deployment_record(_env(ARCHI_CONFIG_PIN_MATCHED=""))
    assert pin_state(record) != PIN_STATE_MATCHED


def test_tracked_edits_prove_a_live_edit_even_without_a_verdict():
    """Dirty paths are positive evidence; they outrank a missing verdict."""
    record = build_deployment_record(
        _env(ARCHI_CONFIG_PIN_MATCHED="", ARCHI_CONFIG_DIRTY_PATHS="M\tlists/sources.list")
    )
    assert pin_state(record) == PIN_STATE_LIVE_EDITED


def test_pin_state_names_the_three_states():
    assert pin_state(build_deployment_record(_env())) == PIN_STATE_MATCHED
    assert (
        pin_state(build_deployment_record(_env(ARCHI_CONFIG_PIN_MATCHED="no")))
        == PIN_STATE_LIVE_EDITED
    )
    assert (
        pin_state(build_deployment_record({})) == PIN_STATE_UNKNOWN
    )


def test_whitespace_only_dirty_paths_are_not_edits():
    record = build_deployment_record(_env(ARCHI_CONFIG_DIRTY_PATHS="  \n "))
    assert is_live_edited(record) is False


# ---------------------------------------------------------------------------
# write_deployment_record
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, raises=False):
        self.raises = raises
        self.executed = []

    def execute(self, sql, params=None):
        if self.raises:
            raise RuntimeError("insert exploded")
        self.executed.append((sql, params))

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, raises=False):
        self.cursor_obj = _FakeCursor(raises)
        self.committed = False

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.committed = True

    def close(self):
        pass


def test_write_inserts_the_record_and_commits():
    conn = _FakeConn()

    assert write_deployment_record(conn, _env()) is True

    assert conn.committed is True
    sql, params = conn.cursor_obj.executed[0]
    assert "INSERT INTO deployment_record" in sql
    assert "deploy-pin-2026-09e" in params


def test_a_failed_write_is_swallowed_so_the_deploy_continues():
    """The config is the product; the record is provenance (design D9)."""
    conn = _FakeConn(raises=True)

    assert write_deployment_record(conn, _env()) is False


def test_a_failed_write_does_not_raise_even_without_a_connection():
    assert write_deployment_record(None, _env()) is False


# ---------------------------------------------------------------------------
# record_deployment (the config-seed entry point)
# ---------------------------------------------------------------------------


class _FakeConfigService:
    """Mimics ConfigService's acquire/release connection pairing."""

    def __init__(self, conn=None, acquire_raises=False):
        self.conn = conn if conn is not None else _FakeConn()
        self.acquire_raises = acquire_raises
        self.released = []

    def _get_connection(self):
        if self.acquire_raises:
            raise RuntimeError("no database")
        return self.conn

    def _release_connection(self, conn):
        self.released.append(conn)


def test_record_deployment_writes_and_releases_the_connection():
    cs = _FakeConfigService()

    assert record_deployment(cs, _env()) is True

    assert cs.released == [cs.conn]
    sql, _ = cs.conn.cursor_obj.executed[0]
    assert "INSERT INTO deployment_record" in sql


def test_record_deployment_releases_the_connection_even_when_the_write_fails():
    """A leaked connection on the seed path would outlive the deploy."""
    cs = _FakeConfigService(conn=_FakeConn(raises=True))

    assert record_deployment(cs, _env()) is False
    assert cs.released == [cs.conn]


def test_record_deployment_survives_a_connection_failure():
    cs = _FakeConfigService(acquire_raises=True)

    assert record_deployment(cs, _env()) is False
    assert cs.released == []

"""Unit tests for Mattermost service start-up (openspec/changes/fix-issue-607-mattermost-startup).

Every test monkeypatches DataManager, archi, get_full_config, read_secret, and
requests in the src.interfaces.mattermost namespace (no network, no Postgres).
"""

from unittest import mock

import pytest


class _StopLoop(Exception):
    """Sentinel used to escape the service's `while True` loop in tests."""


def test_service_mattermost_main_sets_up_factory_before_mattermost(monkeypatch):
    from src.bin import service_mattermost

    order = []
    factory_cls = mock.Mock()

    def from_env(**kwargs):
        order.append(("from_env", kwargs))
        return "F"

    factory_cls.from_env.side_effect = from_env
    factory_cls.set_instance.side_effect = lambda f: order.append(("set", f))

    def make_instance():
        order.append("mattermost")
        instance = mock.Mock()
        instance.mattermost_config = {"update_time": 60}
        instance.process_posts.side_effect = _StopLoop()
        return instance

    mattermost_cls = mock.Mock(side_effect=make_instance)

    monkeypatch.setattr(service_mattermost, "PostgresServiceFactory", factory_cls)
    monkeypatch.setattr(service_mattermost.mattermost, "Mattermost", mattermost_cls)
    monkeypatch.setattr(service_mattermost, "read_secret", lambda name: f"<{name}>")
    monkeypatch.setattr(
        service_mattermost.time, "sleep", lambda *a, **k: order.append("sleep")
    )

    with pytest.raises(_StopLoop):
        service_mattermost.main()

    assert order == [
        "sleep",
        ("from_env", {"password_override": "<PG_PASSWORD>"}),
        ("set", "F"),
        "mattermost",
    ]

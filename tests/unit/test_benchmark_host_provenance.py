import builtins

from src.cli.managers.templates_manager import (
    collect_host_information,
    get_git_information,
)


def test_git_info_yaml_carries_the_host_block():
    result = get_git_information()
    assert "host" in result
    host = result["host"]
    assert isinstance(host, dict)
    assert "hostname" in host
    assert "cpu_model" in host
    assert host["hostname"]


def test_cpu_model_is_none_when_cpuinfo_and_platform_both_fail(monkeypatch):
    real_open = builtins.open

    def patched_open(file, *args, **kwargs):
        if file == "/proc/cpuinfo":
            raise OSError("mocked cpuinfo failure")
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", patched_open)
    monkeypatch.setattr(
        "src.cli.managers.templates_manager.platform.processor", lambda: ""
    )
    result = collect_host_information()
    assert result is not None
    assert result["hostname"]
    assert result["cpu_model"] is None


def test_collect_host_information_returns_none_when_hostname_unreadable(monkeypatch):
    def raise_oserror():
        raise OSError("cannot resolve hostname")

    monkeypatch.setattr(
        "src.cli.managers.templates_manager.socket.getfqdn", raise_oserror
    )
    result = collect_host_information()
    assert result is None

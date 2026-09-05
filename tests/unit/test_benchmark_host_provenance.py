import builtins

import pytest

import src.bin.service_benchmark as sb
from src.bin.service_benchmark import ResultHandler
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


_HOST_CAPTURED_AT = (
    "deploy (`archi create`), on the machine this stack runs on"
    " — a container cannot move hosts, so a --rerun ran here too"
)


@pytest.fixture()
def _sb_isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(ResultHandler, "metadata", {})
    monkeypatch.setattr(ResultHandler, "results", [])
    monkeypatch.setattr(
        ResultHandler, "get_corpus_snapshot_id", staticmethod(lambda: "snap")
    )
    monkeypatch.setattr(
        ResultHandler, "get_corpus_fingerprint", staticmethod(lambda: "sha256:corpus")
    )
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "mod.py").write_bytes(b"body")
    monkeypatch.setattr(sb, "PACKAGE_DIR", str(package))


def test_metadata_records_the_host(monkeypatch, tmp_path, _sb_isolate):
    git_info = tmp_path / "git_info.yaml"
    git_info.write_text("last_commit: abc\nhost:\n  hostname: h1\n  cpu_model: c1\n")
    monkeypatch.setattr(sb, "EXTRA_METADATA_PATH", str(git_info))

    ResultHandler.add_metadata()

    assert ResultHandler.metadata["host"] == {"hostname": "h1", "cpu_model": "c1"}
    assert "host" not in (ResultHandler.metadata["git_info"] or {})
    assert ResultHandler.metadata["host_captured_at"] == _HOST_CAPTURED_AT


def test_metadata_records_null_when_the_deploy_predates_the_field(
    monkeypatch, tmp_path, _sb_isolate
):
    git_info = tmp_path / "git_info.yaml"
    git_info.write_text("last_commit: abc\n")
    monkeypatch.setattr(sb, "EXTRA_METADATA_PATH", str(git_info))

    ResultHandler.add_metadata()

    assert ResultHandler.metadata["host"] is None


def test_metadata_records_null_when_file_is_unreadable(
    monkeypatch, tmp_path, _sb_isolate
):
    monkeypatch.setattr(sb, "EXTRA_METADATA_PATH", str(tmp_path / "absent.yaml"))

    ResultHandler.add_metadata()

    assert ResultHandler.metadata["host"] is None


def test_cpu_model_is_none_when_platform_processor_itself_raises(monkeypatch):
    """Capture never raises: the spec forbids a deploy failing for provenance.

    ``platform.processor()`` reaches ``_Processor.from_subprocess``, which catches
    only ``OSError``/``CalledProcessError`` -- a ``uname -p`` emitting undecodable
    bytes raises ``UnicodeDecodeError`` straight through the helper.
    """
    real_open = builtins.open

    def patched_open(file, *args, **kwargs):
        if file == "/proc/cpuinfo":
            raise OSError("mocked cpuinfo failure")
        return real_open(file, *args, **kwargs)

    def raise_unicode_error():
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    monkeypatch.setattr(builtins, "open", patched_open)
    monkeypatch.setattr(
        "src.cli.managers.templates_manager.platform.processor", raise_unicode_error
    )

    result = collect_host_information()

    assert result is not None
    assert result["hostname"]
    assert result["cpu_model"] is None

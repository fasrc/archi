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

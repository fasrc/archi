"""deploy/scripts/lib.sh: each deployment deploys its own services list.

dev (the GPU host) runs the Slack bot, which needs the chat app's /v1. dev's chat
auth is off, so its /v1 must be local-only: this file also pins that the gate exists
in the same checkout that deploys slack on dev — the order the rollout depends on
(openspec/changes/v1-local-only, design "Config ahead of code").
"""

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
LIB = REPO / "deploy" / "scripts" / "lib.sh"


def _services(deployment: str, **env) -> str:
    """SERVICES as lib.sh resolves it for one deployment name (nothing is deployed)."""
    script = f'source "{LIB}" >/dev/null 2>&1; printf %s "$SERVICES"'
    run_env = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "DEPLOYMENT": deployment}
    run_env.update(env)
    out = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=run_env,
        cwd=REPO,
        check=False,
    )
    return out.stdout


def test_dev_deploys_the_slack_bot():
    assert _services("dev") == "chatbot,slack"


@pytest.mark.parametrize("name", ["claw", "some-other-host"])
def test_other_deployments_keep_chatbot_only(name):
    assert _services(name) == "chatbot"


def test_the_list_is_not_an_environment_knob():
    # Like the pin table: a services list in the environment is invisible to review.
    assert _services("dev", SERVICES="chatbot") == "chatbot,slack"


def test_the_local_only_gate_ships_with_slack_on_dev():
    # dev's /v1 is unauthenticated; this checkout deploys slack (and so /v1) there,
    # so it must carry the gate that keeps /v1 off the network.
    compat = (REPO / "src/interfaces/chat_app/openai_compat.py").read_text()
    template = (REPO / "src/cli/templates/base-config.yaml").read_text()
    assert "def _enforce_local_only" in compat
    assert "services.chat_app.openai_compat.local_only" in template

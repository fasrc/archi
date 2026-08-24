"""Which base images `archi create` must have in hand before it tears anything down.

The preflight exists because the service templates build `FROM` an `a2rchi-*-base` image and
then run `pip install .`. An image below the declared Python floor turns every service build
into a failure -- and under `--force` that failure lands *after* the existing deployment has
been removed unless something refuses first (fasrc/archi#266, #287).
"""

import re
from pathlib import Path

import pytest

from src.cli.managers import base_image_preflight as preflight

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = REPO_ROOT / "src" / "cli" / "templates" / "dockerfiles"


# --- Which images a deployment requires (design D4) -------------------------------------


def test_python_base_is_always_required():
    """Every deployment builds at least one python-base service.

    `config-seed` builds `Dockerfile-chat` irrespective of whether the chatbot is enabled, so
    there is no supported configuration that skips the python base.
    """
    refs = preflight.required_base_images(gpu_ids=None, grader_enabled=False)

    assert any(preflight.PYTHON_BASE in ref for ref in refs), refs


def test_gpu_deployment_also_requires_the_pytorch_base():
    refs = preflight.required_base_images(gpu_ids="all", grader_enabled=False)

    assert any(preflight.PYTORCH_BASE in ref for ref in refs), refs


def test_grader_requires_the_pytorch_base_without_any_gpu():
    """`Dockerfile-grader` is a non-GPU service that builds on the pytorch base.

    This is the case that defeats a "GPU implies pytorch, otherwise python" shortcut: the
    shortcut would skip the one image this deployment actually needs.
    """
    refs = preflight.required_base_images(gpu_ids=None, grader_enabled=True)

    assert any(preflight.PYTORCH_BASE in ref for ref in refs), refs


def test_pytorch_base_is_not_fetched_when_nothing_uses_it():
    """The pytorch base is the expensive image; fetching it speculatively is the cost to avoid."""
    refs = preflight.required_base_images(gpu_ids=None, grader_enabled=False)

    assert not any(preflight.PYTORCH_BASE in ref for ref in refs), refs


def test_required_references_carry_the_pin_from_the_templates():
    """A hard-coded tag would let the preflight and the templates disagree silently."""
    refs = preflight.required_base_images(gpu_ids="all", grader_enabled=True)

    template_refs = set()
    for dockerfile in TEMPLATE_DIR.glob("Dockerfile-*"):
        match = re.search(
            r"^FROM\s+(\S*a2rchi-\w+-base\S*)", dockerfile.read_text(), re.MULTILINE
        )
        if match:
            template_refs.add(match.group(1))

    assert set(refs) <= template_refs, (
        f"preflight returned reference(s) no template declares: "
        f"{sorted(set(refs) - template_refs)}"
    )
    assert all(":" in ref for ref in refs), f"unpinned reference in {refs}"


# --- The rule must keep matching the templates it claims to describe --------------------


def test_two_image_rule_still_matches_every_template():
    """The rule in `required_base_images` is a claim about the templates. Check the claim.

    A new pytorch-based service that is not a `-gpu` variant, or a `-gpu` variant moved onto
    the python base, silently invalidates the rule -- the preflight would then fetch one image
    and the build would need another. Failing here forces design D4 to be revisited.
    """
    offenders = []
    for dockerfile in sorted(TEMPLATE_DIR.glob("Dockerfile-*")):
        match = re.search(
            r"^FROM\s+\S*a2rchi-(\w+)-base", dockerfile.read_text(), re.MULTILINE
        )
        if not match:
            continue
        base, name = match.group(1), dockerfile.name
        is_gpu = name.endswith("-gpu")
        if base == "pytorch" and not (is_gpu or name == "Dockerfile-grader"):
            offenders.append(f"{name}: pytorch base but neither -gpu nor the grader")
        if base == "python" and is_gpu:
            offenders.append(f"{name}: -gpu variant on the python base")

    assert not offenders, (
        f"the two-image rule no longer describes the templates: {offenders} -- "
        f"update design D4 and `required_base_images` together"
    )


# --- The availability decision (design D2, D7) ------------------------------------------

GHCR_REF = "ghcr.io/fasrc/a2rchi-python-base:dev-4314ac4"
LOCAL_REF = "localhost/a2rchi/a2rchi-python-base:dev-4314ac4"


def _decide(**overrides):
    kwargs = {"runtime_available": True, "present_locally": False, "fetch_cause": None}
    kwargs.update(overrides)
    reference = kwargs.pop("reference", GHCR_REF)
    return preflight.decide_availability(reference, **kwargs)


def test_present_locally_is_available_without_any_fetch():
    """A provisioned host must not be made to reach a registry it does not need."""
    outcome = _decide(present_locally=True, fetch_cause=preflight.Cause.UNAUTHORIZED)

    assert outcome.verdict is preflight.Verdict.AVAILABLE
    assert outcome.cause is None


def test_absent_but_pulled_is_available():
    outcome = _decide(present_locally=False, fetch_cause=None)

    assert outcome.verdict is preflight.Verdict.AVAILABLE


@pytest.mark.parametrize(
    "cause",
    [
        preflight.Cause.UNAUTHORIZED,
        preflight.Cause.UNKNOWN_TAG,
        preflight.Cause.UNREACHABLE,
        preflight.Cause.NO_DISK,
    ],
)
def test_a_failed_pull_refuses_and_keeps_its_cause(cause):
    """The cause has to survive the decision, because each one has a different remedy."""
    outcome = _decide(fetch_cause=cause)

    assert outcome.verdict is preflight.Verdict.REFUSED
    assert outcome.cause is cause


def test_absent_localhost_reference_refuses_without_consulting_a_registry():
    """`localhost/` is a registry-style name, not evidence the image exists.

    `build_docker_images.sh` tags locally built bases this way. A fresh or pruned daemon
    resolves the name to nothing, and no registry can supply it.
    """
    outcome = _decide(reference=LOCAL_REF, fetch_cause=None)

    assert outcome.verdict is preflight.Verdict.REFUSED
    assert outcome.cause is preflight.Cause.LOCAL_BUILD_MISSING


def test_no_runtime_refuses_a_real_create():
    """Compose needs the same runtime later, so standing down only moves the failure."""
    outcome = _decide(runtime_available=False, dry=False)

    assert outcome.verdict is preflight.Verdict.REFUSED
    assert outcome.cause is preflight.Cause.NO_RUNTIME


def test_no_runtime_leaves_a_dry_run_unverified_rather_than_refused():
    """`--dry` requires no runtime by an existing decision (cli_main.py:155-160)."""
    outcome = _decide(runtime_available=False, dry=True)

    assert outcome.verdict is preflight.Verdict.UNVERIFIED
    assert outcome.cause is preflight.Cause.NO_RUNTIME


def test_dry_run_cannot_verify_an_image_it_did_not_pull():
    outcome = _decide(dry=True, fetch_cause=None)

    assert outcome.verdict is preflight.Verdict.UNVERIFIED
    assert outcome.cause is preflight.Cause.NOT_PULLED


def test_unsupported_probe_is_unverified_not_refused():
    outcome = _decide(dry=True, fetch_cause=preflight.Cause.PROBE_UNSUPPORTED)

    assert outcome.verdict is preflight.Verdict.UNVERIFIED
    assert outcome.cause is preflight.Cause.PROBE_UNSUPPORTED


def test_a_real_create_has_no_unverified_outcome():
    """The invariant, asserted directly rather than inferred from the cases above.

    Every combination a real create can produce must be available or refused. An unverified
    real create would be the original defect back again: proceeding on an assumption.
    """
    causes = [None] + list(preflight.Cause)
    for present in (True, False):
        for cause in causes:
            for reference in (GHCR_REF, LOCAL_REF):
                outcome = preflight.decide_availability(
                    reference,
                    runtime_available=True,
                    present_locally=present,
                    fetch_cause=cause,
                    dry=False,
                )
                assert outcome.verdict is not preflight.Verdict.UNVERIFIED, (
                    f"real create returned UNVERIFIED for present={present} "
                    f"cause={cause} ref={reference}"
                )


# --- The Python floor (design D5) --------------------------------------------------------


def test_version_at_or_above_the_floor_is_available():
    outcome = preflight.check_python_floor(GHCR_REF, "Python 3.11.9", ">=3.11")

    assert outcome.verdict is preflight.Verdict.AVAILABLE


def test_version_below_the_floor_refuses_and_names_both_numbers():
    """The operator needs to see what the image has AND what the project demands."""
    outcome = preflight.check_python_floor(GHCR_REF, "Python 3.10.20", ">=3.11")

    assert outcome.verdict is preflight.Verdict.REFUSED
    assert outcome.cause is preflight.Cause.VERSION_BELOW_FLOOR

    message = preflight.compose_message(outcome)
    assert "3.10.20" in message
    assert ">=3.11" in message


@pytest.mark.parametrize("reported", [None, "", "not a version", "Python"])
def test_unreadable_version_refuses(reported):
    """Passing this would trade an unknown compatibility result for a teardown."""
    outcome = preflight.check_python_floor(GHCR_REF, reported, ">=3.11")

    assert outcome.verdict is preflight.Verdict.REFUSED
    assert outcome.cause is preflight.Cause.VERSION_UNREADABLE


# --- Diagnostics (design D3, D6) ---------------------------------------------------------


def test_unauthorized_message_names_the_classic_pat_and_sso():
    """A fine-grained token fails identically, so "log in" alone sends them round again."""
    outcome = preflight.Outcome(
        GHCR_REF, preflight.Verdict.REFUSED, preflight.Cause.UNAUTHORIZED
    )
    message = preflight.compose_message(outcome)

    assert "classic" in message.lower()
    assert "read:packages" in message
    assert "sso" in message.lower()
    assert GHCR_REF in message


def test_stale_pin_message_does_not_send_the_operator_to_log_in():
    outcome = preflight.Outcome(
        GHCR_REF, preflight.Verdict.REFUSED, preflight.Cause.UNKNOWN_TAG
    )
    message = preflight.compose_message(outcome)

    assert "login" not in message.lower()
    assert "update_service_base_images" in message


def test_out_of_disk_message_does_not_send_the_operator_to_log_in():
    outcome = preflight.Outcome(
        GHCR_REF, preflight.Verdict.REFUSED, preflight.Cause.NO_DISK
    )
    message = preflight.compose_message(outcome)

    assert "login" not in message.lower()
    assert "disk" in message.lower()


def test_missing_local_build_message_names_the_build_script():
    outcome = preflight.Outcome(
        LOCAL_REF, preflight.Verdict.REFUSED, preflight.Cause.LOCAL_BUILD_MISSING
    )
    message = preflight.compose_message(outcome)

    assert "build_docker_images.sh" in message
    assert "login" not in message.lower()


def test_login_command_matches_the_deployments_container_tool():
    """Telling a podman operator to run `docker login` is a wrong instruction."""
    outcome = preflight.Outcome(
        GHCR_REF, preflight.Verdict.REFUSED, preflight.Cause.UNAUTHORIZED
    )

    assert "podman login" in preflight.compose_message(outcome, "podman")
    assert "docker login" in preflight.compose_message(outcome, "docker")


def test_unverified_messages_say_not_verified():
    for cause in (preflight.Cause.NOT_PULLED, preflight.Cause.PROBE_UNSUPPORTED):
        outcome = preflight.Outcome(GHCR_REF, preflight.Verdict.UNVERIFIED, cause)
        assert "NOT VERIFIED" in preflight.compose_message(outcome)


def test_summary_omits_available_references():
    outcomes = [
        preflight.Outcome(GHCR_REF, preflight.Verdict.AVAILABLE),
        preflight.Outcome(
            LOCAL_REF, preflight.Verdict.REFUSED, preflight.Cause.LOCAL_BUILD_MISSING
        ),
    ]
    summary = preflight.summarize(outcomes)

    assert GHCR_REF not in summary
    assert LOCAL_REF in summary

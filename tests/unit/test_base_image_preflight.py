"""Which base images `archi create` must have in hand before it tears anything down.

The preflight exists because the service templates build `FROM` an `a2rchi-*-base` image and
then run `pip install .`. An image below the declared Python floor turns every service build
into a failure -- and under `--force` that failure lands *after* the existing deployment has
been removed unless something refuses first (fasrc/archi#266, #287).
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from src.cli.managers import base_image_preflight as preflight

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = REPO_ROOT / "src" / "cli" / "templates" / "dockerfiles"
UPDATER = REPO_ROOT / "scripts" / "dev" / "update_service_base_images.py"

_FROM_GHCR = re.compile(r"^FROM (ghcr\.io/fasrc/\S+)", re.MULTILINE)
_DIGEST_REF = re.compile(r"^ghcr\.io/fasrc/[a-z0-9-]+@sha256:[0-9a-f]{64}$")
# The release workflow rewrites every template to the CalVer tag it just built
# (``test-and-build-tag.yml:158``) and commits the result to the dispatched
# branch (:207). That state is deliberate and verified two steps later, so it is
# an accepted pin -- not the mutable ``dev-<sha>`` tag these guards exist to
# keep out.
_RELEASE_REF = re.compile(r"^ghcr\.io/fasrc/[a-z0-9-]+:v\d{4}\.\d{2}\.\d+$")


def _pin_state(reference):
    """Classify one base reference as ``digest``, ``release``, or ``mutable``."""
    if _DIGEST_REF.match(reference):
        return "digest"
    if _RELEASE_REF.match(reference):
        return "release"
    return "mutable"


_IMAGE_NAME = re.compile(r"^ghcr\.io/fasrc/([a-z0-9-]+)[@:]")
_RELEASE_TAG = re.compile(r"^ghcr\.io/fasrc/[a-z0-9-]+:(v\d{4}\.\d{2}\.\d+)$")

KNOWN_BASES = {preflight.PYTHON_BASE, preflight.PYTORCH_BASE}


def _base_references(directory=TEMPLATE_DIR):
    """``(filename, reference)`` for every ``FROM ghcr.io/fasrc/...`` line."""
    references = []
    for path in sorted(directory.glob("Dockerfile-*")):
        for match in _FROM_GHCR.finditer(path.read_text()):
            references.append((path.name, match.group(1)))
    return references


def _image_map(directory=TEMPLATE_DIR):
    """``{template name: base image name}`` -- identity, with the pin stripped.

    ``_pin_state`` deliberately ignores which image a reference names, so on its
    own it cannot tell a correct rewrite from one that pointed the CPU templates
    at the GPU base. This is the half that can.
    """
    mapping = {}
    for name, reference in _base_references(directory):
        match = _IMAGE_NAME.match(reference)
        mapping[name] = match.group(1) if match else reference
    return mapping


# --- Digest pins on the real templates (task 1.1) ----------------------------------------


def test_base_references_are_pinned_to_the_expected_digests():
    """The templates must carry immutable digest references, not mutable tags.

    Both the pattern and the exact digest are asserted so that a tag repin cannot
    slip past this check and a digest swap requires an explicit update here.
    """
    if {_pin_state(ref) for _name, ref in _base_references()} == {"release"}:
        pytest.skip(
            "tree is release-retargeted; the digest constants describe the "
            "development state only"
        )

    python_ref = preflight.base_reference(preflight.PYTHON_BASE)
    pytorch_ref = preflight.base_reference(preflight.PYTORCH_BASE)

    assert python_ref is not None, f"{preflight.PYTHON_BASE} not found in any template"
    assert (
        pytorch_ref is not None
    ), f"{preflight.PYTORCH_BASE} not found in any template"

    digest_re = re.compile(r"@sha256:[0-9a-f]{64}$")
    assert digest_re.search(
        python_ref
    ), f"{preflight.PYTHON_BASE} reference is not digest-pinned: {python_ref!r}"
    assert digest_re.search(
        pytorch_ref
    ), f"{preflight.PYTORCH_BASE} reference is not digest-pinned: {pytorch_ref!r}"

    python_digest = (
        "sha256:c068f17b8cba96682e7007c9dd5511f43fea86c796f3cbeee44e2766c5a9b8e8"
    )
    pytorch_digest = (
        "sha256:c29c6e8b4262736e3a5d3d47756b0d483db88254a91b16932d37f498bc704b5e"
    )
    assert (
        python_digest in python_ref
    ), f"{preflight.PYTHON_BASE} digest mismatch: {python_ref!r}"
    assert (
        pytorch_digest in pytorch_ref
    ), f"{preflight.PYTORCH_BASE} digest mismatch: {pytorch_ref!r}"


# --- Template shape guards (task 1.2) ---------------------------------------------------


def test_no_template_carries_a_mutable_base_reference():
    """A floating tag such as ``dev-4314ac4`` makes the template mutable.

    Two references are immutable enough to ship: a digest, and the CalVer release
    tag the release workflow writes. Everything else is rejected.
    """
    references = _base_references()
    assert references, f"no ghcr base references found in {TEMPLATE_DIR}"

    offenders = [
        f"{name}: {ref}" for name, ref in references if _pin_state(ref) == "mutable"
    ]
    assert not offenders, f"mutable ghcr FROM found in: {offenders}"


def test_all_templates_share_one_pin_state():
    """The pin count equals len(service_templates()), and a split tree means a partial rewrite.

    Counting alone would miss the worse failure: a rewrite that updated some
    templates and not others leaves the count right and the deployment building
    against two different base images.
    """
    references = _base_references()
    expected = len(preflight.service_templates())
    assert (
        len(references) == expected
    ), f"expected {expected} base references, found {len(references)}: {references}"

    states = {_pin_state(ref) for _name, ref in references}
    assert len(states) == 1, f"templates disagree on pin state: {states}"

    assert set(_image_map().values()) <= KNOWN_BASES, (
        "templates name a base image outside "
        f"{sorted(KNOWN_BASES)}: {sorted(set(_image_map().values()))}"
    )

    if states == {"release"}:
        tags = {_RELEASE_TAG.match(ref).group(1) for _name, ref in references}
        assert len(tags) == 1, f"release-retargeted templates disagree on tag: {tags}"


def test_every_digest_pinned_from_line_has_the_annotation_above_it():
    """The annotation line is what update_service_base_images.py uses to locate pins."""
    annotation = (
        "# base-image-pin: dev-4314ac4 (managed by update_service_base_images.py)"
    )
    digest_from_re = re.compile(
        r"^FROM ghcr\.io/fasrc/[a-z0-9-]+@sha256:[0-9a-f]{64}", re.MULTILINE
    )

    dockerfiles = list(TEMPLATE_DIR.glob("Dockerfile-*"))
    assert dockerfiles, f"no Dockerfiles found in {TEMPLATE_DIR}"

    offenders = []
    for f in dockerfiles:
        lines = f.read_text().splitlines()
        for i, line in enumerate(lines):
            if digest_from_re.match(line):
                prev = lines[i - 1] if i > 0 else ""
                if prev != annotation:
                    offenders.append(
                        f"{f.name}:{i + 1} — annotation missing above FROM (got {prev!r})"
                    )

    assert not offenders, f"digest-pinned FROM lines without annotation: {offenders}"


def test_no_from_line_contains_a_hash():
    """A # on a FROM line is a parse error at build time (fasrc/archi#342 regression guard)."""
    dockerfiles = list(TEMPLATE_DIR.glob("Dockerfile-*"))
    assert dockerfiles, f"no Dockerfiles found in {TEMPLATE_DIR}"

    offenders = []
    for f in dockerfiles:
        for i, line in enumerate(f.read_text().splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("FROM") and "#" in stripped:
                offenders.append(f"{f.name}:{i}: {line!r}")

    assert not offenders, f"FROM lines containing '#': {offenders}"


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


# --- Mapping real container-tool failures onto causes (design D3) ------------------------


@pytest.mark.parametrize(
    "stderr, expected",
    [
        ("unauthorized: authentication required", preflight.Cause.UNAUTHORIZED),
        ("denied: permission_denied: read_package", preflight.Cause.UNAUTHORIZED),
        (
            "Error response from daemon: Head ...: 403 Forbidden",
            preflight.Cause.UNAUTHORIZED,
        ),
        ("manifest unknown", preflight.Cause.UNKNOWN_TAG),
        ("manifest for ghcr.io/x:y not found", preflight.Cause.UNKNOWN_TAG),
        ("no space left on device", preflight.Cause.NO_DISK),
        ("write /var/lib/docker: no space left on device", preflight.Cause.NO_DISK),
        ("dial tcp: lookup ghcr.io: no such host", preflight.Cause.UNREACHABLE),
        ("connection refused", preflight.Cause.UNREACHABLE),
        ("i/o timeout", preflight.Cause.UNREACHABLE),
        (
            "docker: 'manifest' is not a docker command",
            preflight.Cause.PROBE_UNSUPPORTED,
        ),
        ('unknown command "manifest" for "podman"', preflight.Cause.PROBE_UNSUPPORTED),
    ],
)
def test_pull_errors_map_to_the_cause_that_names_the_right_remedy(stderr, expected):
    assert preflight.classify_fetch_error(stderr) is expected


def test_an_unrecognised_failure_is_unreachable_not_a_pass():
    """Availability has no unknown outcome. An unfamiliar error must not become success."""
    cause = preflight.classify_fetch_error("something nobody has seen before")

    assert cause is not None
    assert cause is preflight.Cause.UNREACHABLE


# --- The orchestrator (design D1, D5, D7) ------------------------------------------------


class FakeProbe:
    """Stands in for the container tool. Every test injects one; none shells out."""

    def __init__(
        self,
        runtime=True,
        present=(),
        fetch_error=None,
        version="Python 3.11.9",
        tool="docker",
    ):
        self.runtime = runtime
        self.present = set(present)
        self.fetch_error = fetch_error
        self.version = version
        self.container_tool = tool
        self.pulled = []
        self.reachability_checked = []
        self.versions_read = []

    def runtime_available(self):
        return self.runtime

    def image_present(self, reference):
        return reference in self.present

    def pull(self, reference):
        self.pulled.append(reference)
        if self.fetch_error is None:
            self.present.add(reference)
        return self.fetch_error

    def reachable(self, reference):
        self.reachability_checked.append(reference)
        return self.fetch_error

    def python_version(self, reference):
        self.versions_read.append(reference)
        return self.version


def test_a_real_create_pulls_an_absent_image_and_checks_its_version():
    probe = FakeProbe(present=())
    outcomes = preflight.run_preflight(
        [GHCR_REF], probe=probe, floor=">=3.11", dry=False
    )

    assert probe.pulled == [GHCR_REF]
    assert probe.versions_read == [GHCR_REF]
    assert all(o.verdict is preflight.Verdict.AVAILABLE for o in outcomes)


def test_a_present_image_is_not_pulled_but_is_still_version_checked():
    probe = FakeProbe(present=(GHCR_REF,))
    outcomes = preflight.run_preflight(
        [GHCR_REF], probe=probe, floor=">=3.11", dry=False
    )

    assert probe.pulled == []
    assert probe.versions_read == [GHCR_REF]
    assert outcomes[0].verdict is preflight.Verdict.AVAILABLE


def test_a_freshly_pulled_image_below_the_floor_refuses():
    """The clean-host case #266 was filed about: nothing cached, image incompatible."""
    probe = FakeProbe(present=(), version="Python 3.10.20")
    outcomes = preflight.run_preflight(
        [GHCR_REF], probe=probe, floor=">=3.11", dry=False
    )

    assert outcomes[0].verdict is preflight.Verdict.REFUSED
    assert outcomes[0].cause is preflight.Cause.VERSION_BELOW_FLOOR


def test_a_dry_run_never_pulls():
    probe = FakeProbe(present=())
    preflight.run_preflight([GHCR_REF], probe=probe, floor=">=3.11", dry=True)

    assert probe.pulled == []
    assert probe.reachability_checked == [GHCR_REF]


def test_a_dry_run_checks_the_floor_for_an_image_already_present():
    """A cached-but-incompatible base must not be reported as ready.

    Reading a present image's version needs no pull, so declining to check it here was a
    real false-confidence hole, not an unavoidable limit of dry runs.
    """
    probe = FakeProbe(present=(GHCR_REF,), version="Python 3.10.20")
    outcomes = preflight.run_preflight(
        [GHCR_REF], probe=probe, floor=">=3.11", dry=True
    )

    assert probe.pulled == []
    assert outcomes[0].verdict is preflight.Verdict.REFUSED
    assert outcomes[0].cause is preflight.Cause.VERSION_BELOW_FLOOR


def test_a_dry_run_refuses_what_the_real_create_would_refuse():
    probe = FakeProbe(present=(), fetch_error=preflight.Cause.UNAUTHORIZED)
    outcomes = preflight.run_preflight(
        [GHCR_REF], probe=probe, floor=">=3.11", dry=True
    )

    assert outcomes[0].verdict is preflight.Verdict.REFUSED
    assert outcomes[0].cause is preflight.Cause.UNAUTHORIZED


def test_a_dry_run_with_no_runtime_is_unverified_and_reads_no_version():
    probe = FakeProbe(runtime=False)
    outcomes = preflight.run_preflight(
        [GHCR_REF], probe=probe, floor=">=3.11", dry=True
    )

    assert outcomes[0].verdict is preflight.Verdict.UNVERIFIED
    assert outcomes[0].cause is preflight.Cause.NO_RUNTIME
    assert probe.versions_read == []


def test_a_real_create_with_no_runtime_refuses():
    probe = FakeProbe(runtime=False)
    outcomes = preflight.run_preflight(
        [GHCR_REF], probe=probe, floor=">=3.11", dry=False
    )

    assert outcomes[0].verdict is preflight.Verdict.REFUSED
    assert outcomes[0].cause is preflight.Cause.NO_RUNTIME


def test_the_version_probe_is_never_run_on_an_image_that_is_not_present():
    """Reading a version requires the image. Attempting it otherwise would mask the real cause."""
    probe = FakeProbe(present=(), fetch_error=preflight.Cause.UNKNOWN_TAG)
    preflight.run_preflight([GHCR_REF], probe=probe, floor=">=3.11", dry=False)

    assert probe.versions_read == []


# --- The probe seam itself (design D2, D6) -----------------------------------------------


class _FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _stub_subprocess(monkeypatch, handler):
    """Replace subprocess.run so the probe's own command construction is what gets tested."""
    import subprocess

    calls = []

    def _run(args, **kwargs):
        calls.append(list(args))
        return handler(list(args))

    monkeypatch.setattr(subprocess, "run", _run)
    return calls


def test_probe_uses_the_configured_container_tool(monkeypatch):
    """A podman deployment must not shell out to docker."""
    calls = _stub_subprocess(monkeypatch, lambda args: _FakeCompleted(0))
    probe = preflight.ContainerProbe("podman")

    probe.image_present(GHCR_REF)

    assert calls[0][0] == "podman", calls


def test_probe_image_present_follows_the_exit_status(monkeypatch):
    _stub_subprocess(monkeypatch, lambda args: _FakeCompleted(0))
    assert preflight.ContainerProbe("docker").image_present(GHCR_REF) is True

    _stub_subprocess(
        monkeypatch, lambda args: _FakeCompleted(1, stderr="No such image")
    )
    assert preflight.ContainerProbe("docker").image_present(GHCR_REF) is False


def test_probe_pull_returns_none_on_success_and_a_cause_on_failure(monkeypatch):
    _stub_subprocess(monkeypatch, lambda args: _FakeCompleted(0))
    assert preflight.ContainerProbe("docker").pull(GHCR_REF) is None

    _stub_subprocess(
        monkeypatch,
        lambda args: _FakeCompleted(1, stderr="unauthorized: authentication required"),
    )
    assert (
        preflight.ContainerProbe("docker").pull(GHCR_REF)
        is preflight.Cause.UNAUTHORIZED
    )


def test_probe_reachability_reports_an_unsupported_subcommand(monkeypatch):
    _stub_subprocess(
        monkeypatch,
        lambda args: _FakeCompleted(
            1, stderr="docker: 'manifest' is not a docker command"
        ),
    )

    assert (
        preflight.ContainerProbe("docker").reachable(GHCR_REF)
        is preflight.Cause.PROBE_UNSUPPORTED
    )


def test_probe_reads_the_python_version_from_either_stream(monkeypatch):
    _stub_subprocess(
        monkeypatch, lambda args: _FakeCompleted(0, stdout="Python 3.11.9\n")
    )
    assert (
        preflight.ContainerProbe("docker").python_version(GHCR_REF) == "Python 3.11.9"
    )

    # Older CPython builds print the banner on stderr.
    _stub_subprocess(
        monkeypatch, lambda args: _FakeCompleted(0, stderr="Python 3.11.9\n")
    )
    assert (
        preflight.ContainerProbe("docker").python_version(GHCR_REF) == "Python 3.11.9"
    )


def test_probe_version_read_returns_none_when_the_command_fails(monkeypatch):
    _stub_subprocess(monkeypatch, lambda args: _FakeCompleted(127, stderr="not found"))

    assert preflight.ContainerProbe("docker").python_version(GHCR_REF) is None


def test_probe_never_raises_when_the_tool_is_absent(monkeypatch):
    """A missing binary must surface as "no runtime", not as a traceback out of `create`."""

    def _explode(args, **kwargs):
        raise OSError("no such executable")

    import subprocess

    monkeypatch.setattr(subprocess, "run", _explode)
    probe = preflight.ContainerProbe("docker")

    assert probe.runtime_available() is False
    assert probe.image_present(GHCR_REF) is False
    assert probe.pull(GHCR_REF) is preflight.Cause.UNREACHABLE
    assert probe.reachable(GHCR_REF) is preflight.Cause.PROBE_UNSUPPORTED
    assert probe.python_version(GHCR_REF) is None


def test_probe_runtime_available_falls_back_to_version_flag(monkeypatch):
    """Some tools do not implement `version --format`; the fallback keeps them usable."""

    def _handler(args):
        if "--format" in args:
            return _FakeCompleted(1, stderr="unknown flag")
        return _FakeCompleted(0, stdout="podman version 5.4.0")

    _stub_subprocess(monkeypatch, _handler)

    assert preflight.ContainerProbe("podman").runtime_available() is True


# --- Remaining diagnostics and the entry point ------------------------------------------


def test_unreachable_and_no_runtime_messages_name_their_own_causes():
    unreachable = preflight.Outcome(
        GHCR_REF, preflight.Verdict.REFUSED, preflight.Cause.UNREACHABLE
    )
    assert "network" in preflight.compose_message(unreachable).lower()

    no_runtime = preflight.Outcome(
        GHCR_REF, preflight.Verdict.REFUSED, preflight.Cause.NO_RUNTIME
    )
    assert "podman" in preflight.compose_message(no_runtime, "podman")


def test_unreadable_version_message_quotes_what_it_actually_got():
    outcome = preflight.Outcome(
        GHCR_REF,
        preflight.Verdict.REFUSED,
        preflight.Cause.VERSION_UNREADABLE,
        detail="Pythonn 3",
    )

    assert "Pythonn 3" in preflight.compose_message(outcome)


def test_a_causeless_outcome_still_renders():
    """Defensive: a message is never allowed to crash the refusal it is explaining."""
    outcome = preflight.Outcome(GHCR_REF, preflight.Verdict.AVAILABLE)

    assert GHCR_REF in preflight.compose_message(outcome)


def test_base_reference_returns_none_for_an_image_no_template_names(tmp_path):
    (tmp_path / "Dockerfile-chat").write_text(
        "FROM ghcr.io/fasrc/a2rchi-python-base:x\n"
    )

    assert preflight.base_reference("a2rchi-nonesuch-base", tmp_path) is None


def test_required_base_images_refuses_when_no_template_declares_a_base_reference(
    tmp_path,
):
    """A service template with no a2rchi-*-base reference is uncoverable; the preflight
    refuses rather than returning an empty list and passing silently on an assumption.
    """
    (tmp_path / "Dockerfile-chat").write_text("FROM docker.io/library/python:3.11\n")

    with pytest.raises(preflight.BaseImagePreflightError):
        preflight.required_base_images(None, False, tmp_path)


def test_declared_python_floor_reads_the_projects_own_pyproject(tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nrequires-python = ">=3.12"\n')

    assert preflight.declared_python_floor(pyproject) == ">=3.12"


class _Plan:
    def __init__(self, gpu_ids=None, grader=False, raises=False):
        self.gpu_ids = gpu_ids
        self._grader = grader
        self._raises = raises

    def get_service(self, name):
        if self._raises:
            raise ValueError(f"Unknown service: {name}")
        return type("S", (), {"enabled": self._grader})()


def test_enforce_raises_with_the_operator_message_when_a_reference_is_refused():
    probe = FakeProbe(present=(), fetch_error=preflight.Cause.UNAUTHORIZED)

    with pytest.raises(preflight.BaseImagePreflightError) as excinfo:
        preflight.enforce_base_images(_Plan(), probe=probe)

    assert "read:packages" in str(excinfo.value)


def test_enforce_names_podman_in_the_remedy_for_a_podman_deployment():
    probe = FakeProbe(present=(), fetch_error=preflight.Cause.UNAUTHORIZED)

    with pytest.raises(preflight.BaseImagePreflightError) as excinfo:
        preflight.enforce_base_images(_Plan(), probe=probe, use_podman=True)

    assert "podman login" in str(excinfo.value)


def test_enforce_treats_an_unknown_grader_service_as_disabled():
    """A plan without a grader must not crash the preflight looking for one."""
    probe = FakeProbe(present=())

    outcomes = preflight.enforce_base_images(_Plan(raises=True), probe=probe)

    assert all(preflight.PYTORCH_BASE not in o.reference for o in outcomes)


def test_enforce_checks_the_pytorch_base_when_the_grader_is_enabled():
    probe = FakeProbe(present=())

    outcomes = preflight.enforce_base_images(_Plan(grader=True), probe=probe)

    assert any(preflight.PYTORCH_BASE in o.reference for o in outcomes)


def test_unverified_notes_lists_only_unverified_references():
    outcomes = [
        preflight.Outcome(GHCR_REF, preflight.Verdict.AVAILABLE),
        preflight.Outcome(
            LOCAL_REF, preflight.Verdict.UNVERIFIED, preflight.Cause.NO_RUNTIME
        ),
    ]

    notes = preflight.unverified_notes(outcomes)

    assert len(notes) == 1
    assert LOCAL_REF in notes[0]


# --- Reading the declared floor without depending on a source checkout -------------------


def test_declared_floor_prefers_the_source_pyproject_over_installed_metadata():
    """Installed metadata can be stale, and here it demonstrably is.

    This environment's `archi` distribution advertises `Requires-Python: >=3.7`, left over
    from before the floor was corrected to `>=3.11`. A preflight that trusted that number
    would accept the very Python 3.10 base image this whole change exists to reject, and it
    would do so silently. The source tree is the authority whenever it is available.
    """
    floor = preflight.declared_python_floor()

    from packaging.specifiers import SpecifierSet
    from packaging.version import Version

    assert not SpecifierSet(floor).contains(Version("3.10.20")), (
        f"declared floor {floor!r} admits Python 3.10, which is the interpreter "
        f"fasrc/archi#266 is about"
    )


def test_declared_floor_raises_a_named_error_when_nothing_declares_it(monkeypatch):
    """Better an explicit failure than a silently permissive floor."""
    monkeypatch.setattr(
        preflight, "_source_pyproject", lambda: Path("/nonexistent/pyproject.toml")
    )
    monkeypatch.setattr(preflight, "_metadata_python_floor", lambda: None)

    with pytest.raises(preflight.BaseImagePreflightError) as excinfo:
        preflight.declared_python_floor()

    assert "requires-python" in str(excinfo.value).lower()


# --- Timeouts are not the same thing as an unsupported command ---------------------------


def test_a_reachability_timeout_is_unreachable_not_unsupported(monkeypatch):
    """A wedged `manifest inspect` is a registry problem, not a missing subcommand.

    Reporting it as "this container tool does not support the probe" sends the operator to
    look for a tooling fix that does not exist, and hides a degraded registry.
    """
    import subprocess

    def _timeout(args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args, timeout=1)

    monkeypatch.setattr(subprocess, "run", _timeout)

    assert (
        preflight.ContainerProbe("docker").reachable(GHCR_REF)
        is preflight.Cause.UNREACHABLE
    )


def test_a_pull_timeout_is_unreachable(monkeypatch):
    import subprocess

    def _timeout(args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args, timeout=1)

    monkeypatch.setattr(subprocess, "run", _timeout)

    assert (
        preflight.ContainerProbe("docker").pull(GHCR_REF) is preflight.Cause.UNREACHABLE
    )


# --- A base the rule requires but no template declares (round 2 finding) ------------------


def test_enforce_refuses_when_a_required_base_cannot_be_resolved(tmp_path):
    """An unresolvable reference must refuse, not quietly shrink the checked set.

    Dropping it was a silent bypass: a template rename, a packaging mistake, or drift in the
    `FROM` regex would disable the preflight, and `create --force` would then tear down a
    working deployment having proved nothing at all. That is precisely the assumption-passing
    this module forbids.
    """
    (tmp_path / "Dockerfile-chat").write_text("FROM docker.io/library/python:3.11\n")
    probe = FakeProbe()

    with pytest.raises(preflight.BaseImagePreflightError) as excinfo:
        preflight.enforce_base_images(_Plan(), probe=probe, template_dir=tmp_path)

    assert preflight.PYTHON_BASE in str(excinfo.value)


def test_enforce_refuses_when_only_the_pytorch_base_is_missing(tmp_path):
    """A partially resolvable set is still a bypass for the part that went missing."""
    (tmp_path / "Dockerfile-chat").write_text(
        "FROM ghcr.io/fasrc/a2rchi-python-base:dev-4314ac4\n"
    )
    probe = FakeProbe()

    with pytest.raises(preflight.BaseImagePreflightError) as excinfo:
        preflight.enforce_base_images(
            _Plan(grader=True), probe=probe, template_dir=tmp_path
        )

    assert preflight.PYTORCH_BASE in str(excinfo.value)


def test_enforce_refuses_a_missing_reference_on_a_dry_run_too(tmp_path):
    (tmp_path / "Dockerfile-chat").write_text("FROM docker.io/library/python:3.11\n")

    with pytest.raises(preflight.BaseImagePreflightError):
        preflight.enforce_base_images(
            _Plan(), probe=FakeProbe(), template_dir=tmp_path, dry=True
        )


def test_a_grader_lookup_failure_is_not_silently_treated_as_disabled():
    """Only "no such service" may be tolerated; anything else is a real bug worth surfacing.

    Swallowing every exception here would skip the pytorch check for a grader deployment,
    landing on exactly the teardown-then-fail behaviour this change removes.
    """

    class _Exploding:
        gpu_ids = None

        def get_service(self, name):
            raise RuntimeError("plan is corrupt")

    with pytest.raises(RuntimeError):
        preflight.enforce_base_images(_Exploding(), probe=FakeProbe())


def test_a_grader_lookup_keyerror_is_not_silently_treated_as_disabled():
    """A `KeyError` is plan corruption, not evidence that the grader is absent.

    Both `ComposeConfig.get_service` implementations signal an unknown name with
    `ValueError` (`service_registry.py:213`, `service_builder.py:110`), so nothing on the
    tolerated path raises `KeyError`. A `KeyError` reaching here means a dict-backed plan
    lost a key -- and turning that into `grader_enabled = False` drops the pytorch base
    from the checked set, so a `--force` create tears the deployment down and only then
    fails building the grader.
    """

    class _MissingKey:
        gpu_ids = None

        def get_service(self, name):
            raise KeyError(name)

    with pytest.raises(KeyError):
        preflight.enforce_base_images(_MissingKey(), probe=FakeProbe())


def test_an_absent_grader_service_is_still_tolerated():
    """The case the catch actually exists for: a plan that has no grader at all."""
    outcomes = preflight.enforce_base_images(_Plan(raises=True), probe=FakeProbe())

    assert all(preflight.PYTORCH_BASE not in o.reference for o in outcomes)


# --- A present but unreadable pyproject must fail closed (round 3 finding) ---------------


def test_a_malformed_source_pyproject_refuses_rather_than_using_stale_metadata(
    tmp_path, monkeypatch
):
    """Falling back here would re-authorize the very interpreter this module rejects.

    Installed metadata in this environment still says `>=3.7`. If a truncated file, a merge
    artifact, or a permissions problem made the checkout's pyproject unreadable, silently
    reaching for that stale number would approve a Python 3.10 base image -- the precise
    failure fasrc/archi#266 exists to prevent, arrived at by a different route.
    """
    broken = tmp_path / "pyproject.toml"
    broken.write_text("[project\nrequires-python = ")
    monkeypatch.setattr(preflight, "_source_pyproject", lambda: broken)

    with pytest.raises(preflight.BaseImagePreflightError) as excinfo:
        preflight.declared_python_floor()

    assert str(broken) in str(excinfo.value)


def test_a_source_pyproject_without_a_declared_floor_refuses(tmp_path, monkeypatch):
    valid_but_silent = tmp_path / "pyproject.toml"
    valid_but_silent.write_text('[project]\nname = "archi"\n')
    monkeypatch.setattr(preflight, "_source_pyproject", lambda: valid_but_silent)

    with pytest.raises(preflight.BaseImagePreflightError):
        preflight.declared_python_floor()


def test_an_explicitly_supplied_pyproject_that_is_malformed_refuses(tmp_path):
    """An explicit path is a caller's assertion about where the floor lives. Honour it."""
    broken = tmp_path / "pyproject.toml"
    broken.write_text("nonsense {{{")

    with pytest.raises(preflight.BaseImagePreflightError):
        preflight.declared_python_floor(broken)


def test_metadata_is_used_only_when_there_is_no_source_pyproject(monkeypatch):
    """The fallback stays available for a genuine installed CLI, which has no checkout.

    Both sides are stubbed on purpose. An earlier version of this test asserted that *some*
    floor came back without stubbing the metadata lookup, which only held where `archi`
    happens to be installed as a distribution -- true locally, false on CI, where the gate
    installs no archi package and pytest runs straight from the checkout. A test that reads
    ambient environment state is not testing the fallback.
    """
    monkeypatch.setattr(
        preflight, "_source_pyproject", lambda: Path("/nonexistent/pyproject.toml")
    )
    monkeypatch.setattr(preflight, "_metadata_python_floor", lambda: ">=3.11")

    assert preflight.declared_python_floor() == ">=3.11"


# --- The released tree is a legitimate state (review round 1) ----------------------------


def _materialize_release_retargeted_tree(tmp_path):
    """A copy of the templates after the release workflow's retarget step.

    ``update_service_base_images.py`` resolves its target directory from its own
    location (``scripts/dev/...`` -> ``parents[2]``), so copying the script and
    the templates into a temporary tree with the same relative layout runs the
    REAL rewrite against throwaway files. Re-implementing the rewrite here would
    only test the re-implementation.
    """
    scripts_dir = tmp_path / "scripts" / "dev"
    templates_dir = tmp_path / "src" / "cli" / "templates" / "dockerfiles"
    scripts_dir.mkdir(parents=True)
    templates_dir.mkdir(parents=True)
    shutil.copy(UPDATER, scripts_dir / UPDATER.name)
    for path in TEMPLATE_DIR.glob("Dockerfile-*"):
        shutil.copy(path, templates_dir / path.name)

    subprocess.run(
        [
            sys.executable,
            str(scripts_dir / UPDATER.name),
            "--tag",
            "v2026.08.0",
            "--switch-source",
            "ghcr",
            "--orig-tag",
            "all",
        ],
        check=True,
        capture_output=True,
    )
    return templates_dir


def test_the_guards_accept_a_release_retargeted_tree(tmp_path):
    """A released tree must not turn the unit suite red.

    ``test-and-build-tag.yml:158`` rewrites all 15 templates to
    ``ghcr.io/fasrc/...:<release-tag>`` and the step at :207 commits them to the
    dispatched branch. That state is deliberate -- the next step verifies it, and
    the release smoke test builds against it. A guard that calls it mutable makes
    the released branch red and starts every follow-up PR against it red too.
    """
    templates_dir = _materialize_release_retargeted_tree(tmp_path)

    references = _base_references(templates_dir)
    assert len(references) == len(preflight.service_templates(templates_dir))

    mutable = [name for name, ref in references if _pin_state(ref) == "mutable"]
    assert not mutable, f"release-retargeted templates read as mutable: {mutable}"
    assert {_pin_state(ref) for _name, ref in references} == {"release"}

    # Shape is not enough. A rewrite that pointed every template at one base
    # would satisfy every assertion above and ship a deployment building the CPU
    # services on the GPU image. Compare the identity map against the tree the
    # rewrite started from, so the check is "unchanged except for the pin".
    assert _image_map(templates_dir) == _image_map()

    tags = {_RELEASE_TAG.match(ref).group(1) for _name, ref in references}
    assert tags == {"v2026.08.0"}


def test_the_release_guard_detects_a_rewrite_to_the_wrong_base(tmp_path):
    """Proof that the identity comparison above discriminates.

    One template flipped from the python base to the pytorch base: every
    shape-level assertion still holds, and the identity map has to catch it.
    """
    templates_dir = _materialize_release_retargeted_tree(tmp_path)
    victim = templates_dir / "Dockerfile-chat"
    victim.write_text(
        victim.read_text().replace(preflight.PYTHON_BASE, preflight.PYTORCH_BASE)
    )

    references = _base_references(templates_dir)
    assert {_pin_state(ref) for _name, ref in references} == {"release"}
    assert len(references) == len(preflight.service_templates(templates_dir))

    assert _image_map(templates_dir) != _image_map()


# --- The declared service-template set (tasks.md 1.1) ------------------------------------


def test_stale_template_exclusions_reports_absent_exclusions_not_present_ones(tmp_path):
    """An exclusion key with no matching file is stale; one with a file is honest."""
    (tmp_path / "Dockerfile-base").write_text("FROM scratch\n")
    # Only Dockerfile-base exists; Dockerfile-base-gpu does not.
    stale = preflight.stale_template_exclusions(tmp_path)
    assert "Dockerfile-base-gpu" in stale
    assert "Dockerfile-base" not in stale


def test_stale_template_exclusions_on_the_real_directory_is_empty():
    """Every key in NON_SERVICE_TEMPLATES must name an actual file on disk."""
    stale = preflight.stale_template_exclusions()
    assert not stale, (
        f"NON_SERVICE_TEMPLATES contains stale entries (no matching file): "
        + ", ".join(sorted(stale))
    )


def test_service_templates_has_15_of_19_and_excluded_names_match_the_declaration():
    """service_templates() returns 15 of the 19 Dockerfile* files; the 4 excluded names
    are exactly the keys of NON_SERVICE_TEMPLATES."""
    all_dockerfiles = sorted(preflight.TEMPLATE_DIR.glob("Dockerfile*"))
    services = preflight.service_templates()
    assert (
        len(all_dockerfiles) == 19
    ), f"expected 19 Dockerfile* files, found {len(all_dockerfiles)}: " + ", ".join(
        p.name for p in all_dockerfiles
    )
    assert (
        len(services) == 15
    ), f"expected 15 service templates, got {len(services)}: " + ", ".join(
        p.name for p in services
    )
    excluded = {p.name for p in all_dockerfiles} - {p.name for p in services}
    assert excluded == set(preflight.NON_SERVICE_TEMPLATES.keys()), (
        f"excluded names {sorted(excluded)!r} do not match "
        f"NON_SERVICE_TEMPLATES keys {sorted(preflight.NON_SERVICE_TEMPLATES.keys())!r}"
    )


# --- Service templates without a base reference (tasks.md 1.2) ---------------------------

_PINNED_FROM = (
    "FROM ghcr.io/fasrc/a2rchi-python-base"
    "@sha256:c068f17b8cba96682e7007c9dd5511f43fea86c796f3cbeee44e2766c5a9b8e8\n"
)
_THIRD_PARTY_FROM = "FROM docker.io/library/python:3.11\n"


def test_templates_missing_base_reference_reports_replaced_line(tmp_path):
    """A template whose FROM names a third-party image is reported; a correctly pinned
    one is not."""
    (tmp_path / "Dockerfile-service-a").write_text(_PINNED_FROM)
    (tmp_path / "Dockerfile-service-b").write_text(_THIRD_PARTY_FROM)
    missing = preflight.templates_missing_base_reference(tmp_path)
    assert len(missing) == 1
    assert (
        missing[0] == tmp_path / "Dockerfile-service-b"
    ), f"expected Dockerfile-service-b to be reported, got {missing}"


def test_templates_missing_base_reference_reports_deleted_line(tmp_path):
    """A template with no FROM line at all (base removed, not replaced) is also reported."""
    (tmp_path / "Dockerfile-service-a").write_text(_PINNED_FROM)
    (tmp_path / "Dockerfile-service-b").write_text("RUN echo hello\n")
    missing = preflight.templates_missing_base_reference(tmp_path)
    assert len(missing) == 1
    assert (
        missing[0] == tmp_path / "Dockerfile-service-b"
    ), f"expected Dockerfile-service-b to be reported, got {missing}"


def test_templates_missing_base_reference_on_real_directory_is_empty():
    """Every service template must reference an a2rchi-*-base image."""
    missing = preflight.templates_missing_base_reference()
    assert (
        not missing
    ), "Service templates without an a2rchi-*-base FROM reference: " + ", ".join(
        str(p) for p in missing
    )


# --- Deploy preflight: refusing an uncoverable service template (tasks.md 3.1) ----------


def test_required_base_images_refuses_and_names_a_template_with_no_base_reference(
    tmp_path,
):
    """A service template carrying no a2rchi-*-base FROM line causes required_base_images
    to refuse; the refusal names the template so the next reader has the diagnosis."""
    (tmp_path / "Dockerfile-chat").write_text(_PINNED_FROM)
    (tmp_path / "Dockerfile-broken").write_text("FROM docker.io/library/python:3.11\n")

    with pytest.raises(preflight.BaseImagePreflightError) as exc_info:
        preflight.required_base_images(
            gpu_ids=None, grader_enabled=False, template_dir=tmp_path
        )

    assert str(tmp_path / "Dockerfile-broken") in str(exc_info.value), (
        "exception message must name the uncoverable template; "
        f"got: {exc_info.value}"
    )


def test_required_base_images_returns_unchanged_references_when_all_templates_covered(
    tmp_path,
):
    """A directory where every service template carries an a2rchi-*-base reference returns
    exactly the references it returned before the uncoverable-template guard — a correct
    tree's deploy behavior is provably unchanged."""
    python_ref = "ghcr.io/fasrc/a2rchi-python-base@sha256:" + "a" * 64
    pytorch_ref = "ghcr.io/fasrc/a2rchi-pytorch-base@sha256:" + "b" * 64
    (tmp_path / "Dockerfile-chat").write_text(f"FROM {python_ref}\nRUN pip install .\n")
    (tmp_path / "Dockerfile-grader").write_text(
        f"FROM {pytorch_ref}\nRUN pip install .\n"
    )

    refs = preflight.required_base_images(
        gpu_ids=None, grader_enabled=False, template_dir=tmp_path
    )
    assert refs == [python_ref]

    refs_gpu = preflight.required_base_images(
        gpu_ids="all", grader_enabled=True, template_dir=tmp_path
    )
    assert refs_gpu == [python_ref, pytorch_ref]

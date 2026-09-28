"""Installer plans describe changes, never certify a model or distro runtime."""
import copy
import json

import pytest

from h3server.installer.planner import (
    Approval, Inspection, ReleaseArtifact, build_plan, inspect_prerequisites,
    validate_approval, validate_plan,
)
from h3server.probe import CommandResult
from h3server.store import ApprovalRequired, canonical_plan_digest

PROFILE = "h3-int8-group-offload-experimental"


def facts(**kw):
    values = dict(distribution="ubuntu", version="24.04", architecture="x86_64",
                  python_ready=True, driver_working=True, nvidia_device=True,
                  secure_boot="disabled", sudo_available=True,
                  selector_available=True, driver_package=None,
                  packages=(), package_changes=(), issues=())
    values.update(kw)
    return Inspection(**values)


def plan(**kw):
    return build_plan(facts(**kw), profile=PROFILE,
                      artifact=ReleaseArtifact("v1", "h3server-0.1.0-py3-none-any.whl", "a" * 64))


def approval(p, **kw):
    values = dict(plan_digest=canonical_plan_digest(p), license_acknowledged=True,
                  eligibility_confirmed=True, privileged=False, reboot=False)
    values.update(kw)
    return Approval(**values)


@pytest.mark.parametrize("distro,version", [("ubuntu", "24.04"), ("ubuntu", "26.04"), ("debian", "12"), ("debian", "13")])
def test_matrix_does_not_certify_profile(distro, version):
    p = plan(distribution=distro, version=version)
    assert not p["blockers"]
    assert p["profile_confidence"] == "experimental"
    assert p["model_ready"] is p["generation_tested"] is False
    assert validate_plan(json.loads(json.dumps(p))) == p


@pytest.mark.parametrize("kw", [dict(version="22.04"), dict(architecture="aarch64"), dict(distribution="mint")])
def test_unsupported_never_guesses_apt(kw):
    p = plan(**kw)
    assert p["blockers"]
    with pytest.raises(ApprovalRequired):
        validate_approval(p, approval(p, privileged=True))


def test_working_driver_preserved_and_no_toolkit():
    p = plan(secure_boot="enabled")
    assert p["driver_action"] == "preserve"
    assert not p["reboot_required"]
    assert "cuda-toolkit" not in json.dumps(p)
    assert p["requirements"][0].endswith("5ff8e59ff9fe81c6e2df4fb4c6ea0d97a5df5ab2")
    assert "huggingface-hub>=1.32.0,<2.0" in p["requirements"]


def test_digest_binds_all_inputs_and_scopes_are_separate():
    p = plan(packages=("git=1:2.0",), package_changes=("Inst git (1:2.0 Debian:12 [amd64])",))
    with pytest.raises(ApprovalRequired, match="privileged"):
        validate_approval(p, approval(p))
    validate_approval(p, approval(p, privileged=True))
    for field, value in [("profile", "other"), ("packages", []), ("artifact", {})]:
        changed = copy.deepcopy(p)
        changed[field] = value
        with pytest.raises((ApprovalRequired, ValueError)):
            validate_approval(changed, approval(p, privileged=True))
    with pytest.raises(ApprovalRequired, match="eligibility"):
        validate_approval(p, approval(p, privileged=True, eligibility_confirmed=False))


def test_missing_selector_is_discovery_not_ready():
    p = plan(driver_working=False, selector_available=False,
             packages=("ubuntu-drivers-common=1",), package_changes=("Inst ubuntu-drivers-common (1 Ubuntu [amd64])",))
    assert p["purpose"] == "prerequisites"
    assert p["stages"] == ["system-packages", "replan"]
    assert not p["installation_ready"]


def test_secure_boot_and_missing_debian_sources_block():
    p = plan(driver_working=False, driver_package="nvidia-driver-580", secure_boot="enabled")
    assert any("enrollment" in b for b in p["blockers"])
    p = plan(distribution="debian", version="12", driver_working=False,
             issues=("Debian non-free/non-free-firmware repository prerequisite requires explicit administrator approval; sources are never modified.",))
    assert any("non-free" in b for b in p["blockers"])


def test_inspect_fixed_commands_preserves_driver_and_simulates_packages():
    seen = []
    def run(argv, *, timeout):
        seen.append(argv)
        data = {
            ("cat", "/etc/os-release"): 'ID=ubuntu\nVERSION_ID="24.04"',
            ("uname", "-m"): "x86_64",
            ("nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"): "580.1",
            ("lspci", "-Dn"): "0000:01:00.0 0300: 10de:2684",
            ("mokutil", "--sb-state"): "SecureBoot enabled",
        }
        if argv[0] == "dpkg-query":
            return CommandResult("install ok installed\t1\n")
        return CommandResult(data.get(argv, ""), returncode=0)
    f = inspect_prerequisites(run)
    assert f.driver_working and f.nvidia_device
    assert f.packages == ()
    assert not any("ubuntu-drivers" in x or "nvidia-detect" in x for x in seen)
    assert all(isinstance(x, tuple) for x in seen)


def test_plan_rejects_arbitrary_operations():
    p = plan()
    p["requirements"].append("https://attacker.invalid/x.whl")
    with pytest.raises(ValueError):
        validate_plan(p)


def test_plan_cannot_replace_working_driver_through_transitive_packages():
    with pytest.raises(ValueError, match="preserved"):
        plan(packages=("git=1",), package_changes=("Inst libnvidia-gl-580 (580 Ubuntu [amd64])",))


@pytest.mark.parametrize("distro,version,selector,driver", [
    ("ubuntu", "26.04", "driver : nvidia-driver-580-open - distro non-free recommended", "nvidia-driver-580-open"),
    ("debian", "13", "It is recommended to install the\n    nvidia-driver\npackage.", "nvidia-driver"),
])
def test_inspection_selects_only_distro_recommended_driver(distro, version, selector, driver):
    def run(argv, *, timeout):
        if argv == ("cat", "/etc/os-release"):
            return CommandResult(f'ID={distro}\nVERSION_ID="{version}"')
        if argv == ("uname", "-m"):
            return CommandResult("x86_64")
        if argv[0] == "nvidia-smi":
            return CommandResult(returncode=1)
        if argv[0] == "lspci":
            return CommandResult("0000:01:00.0 0300: 10de:2684")
        if argv[0] == "mokutil":
            return CommandResult("SecureBoot disabled")
        if argv[0] in ("ubuntu-drivers", "nvidia-detect"):
            return CommandResult(selector)
        if argv[0] == "dpkg-query":
            return CommandResult("install ok installed\t1\n")
        if argv[0] == "apt-cache":
            assert argv[-1] == driver
            return CommandResult("  Candidate: 580.2-1\n")
        if argv[0] == "apt-get":
            return CommandResult(f"Inst {driver} (580.2-1 distro [amd64])\n")
        return CommandResult()
    f = inspect_prerequisites(run)
    assert f.driver_package == driver
    assert f.packages == (driver + "=580.2-1",)
    p = build_plan(f, profile=PROFILE, artifact=ReleaseArtifact("v1", "h3server-0.1.0-py3-none-any.whl", "a" * 64))
    assert not p["blockers"]
    assert p["reboot_required"]
    validate_approval(p, approval(p, privileged=True, reboot=False))


def test_missing_indexes_only_update_then_replan():
    def run(argv, *, timeout):
        if argv[0] == "cat":
            return CommandResult('ID=ubuntu\nVERSION_ID="24.04"')
        if argv[0] == "uname":
            return CommandResult("x86_64")
        if argv[0] == "nvidia-smi":
            return CommandResult("580.1")
        if argv[0] in ("dpkg-query", "apt-cache", "python3"):
            return CommandResult(returncode=1)
        return CommandResult()
    f = inspect_prerequisites(run)
    p = build_plan(f, profile=PROFILE, artifact=ReleaseArtifact("v1", "h3server-0.1.0-py3-none-any.whl", "a" * 64))
    assert p["refresh_metadata"] and p["packages"] == []
    assert p["purpose"] == "prerequisites"
    assert not p["blockers"]


def test_reboot_gate_never_reuses_general_privileged_approval():
    from h3server.installer.planner import validate_reboot_approval
    p = plan(driver_working=False, driver_package="nvidia-driver-580",
             packages=("nvidia-driver-580=1",), package_changes=("Inst nvidia-driver-580 (1 Ubuntu [amd64])",))
    with pytest.raises(ApprovalRequired, match="reboot"):
        validate_reboot_approval(p, approval(p, privileged=True))
    validate_reboot_approval(p, approval(p, privileged=True, reboot=True))

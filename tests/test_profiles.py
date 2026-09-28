"""Synthetic profile inputs exercise policy; none are H3 memory guarantees."""

from dataclasses import replace
import importlib

import pytest

from h3server.domain import GPUInfo, HardwareInfo
from test_probe import GIB, scan


def api():
    assert importlib.util.find_spec("h3server.profiles") is not None, "profiles implementation missing"
    return importlib.import_module("h3server.profiles")


def profile(**changes):
    p = api()
    values = dict(
        name="synthetic-test-only", status=p.RecommendationStatus.EXPERIMENTAL,
        evidence=("Synthetic test requirements, not measured H3 requirements",),
        distributions=(("ubuntu", "24.04"), ("debian", "12")),
        architectures=("x86_64",), gpu_names=("NVIDIA GeForce RTX 4090",),
        min_gpu_count=1, min_gpu_vram_bytes=20 * GIB, min_ram_bytes=16 * GIB,
        disk=p.DiskEstimate(10 * GIB, ("Synthetic disk estimate for policy testing",)),
        driver_versions=("550.54.14",), service_managers=("systemd",),
        requires_cuda_runtime=True,
    )
    return p.Profile(**(values | changes))


def evaluated(fixture="rtx4090", *, ready=False, **changes):
    hw = scan(fixture).hardware
    if ready:
        hw = replace(hw, cuda_available=True, evidence=hw.evidence + ("Synthetic external runtime test passed",))
    return api().evaluate_profile(hw, profile(**changes))


def test_profiles_no_builtin_verified_inference_profiles():
    assert api().recommend_profiles(scan().hardware) == ()


def test_profiles_4090_does_not_automatically_imply_h3_fit():
    result = evaluated()
    assert result.status == api().RecommendationStatus.UNKNOWN
    assert any("runtime" in reason.lower() for reason in result.reasons)
    assert result.profile_name == "synthetic-test-only"
    assert result.evidence


def test_profiles_all_four_confidence_states():
    p = api()
    assert evaluated(ready=True).status == p.RecommendationStatus.EXPERIMENTAL
    assert evaluated(ready=True, status=p.RecommendationStatus.UNKNOWN).status == p.RecommendationStatus.UNKNOWN
    assert evaluated(ready=True, status=p.RecommendationStatus.UNSUPPORTED).status == p.RecommendationStatus.UNSUPPORTED
    verified = evaluated(ready=True, status=p.RecommendationStatus.VERIFIED,
                         backend_revision="fixture-revision", verification_evidence=("Synthetic generation test record",))
    assert verified.status == p.RecommendationStatus.VERIFIED
    assert any("not" in reason.lower() and "guarantee" in reason.lower() for reason in verified.reasons)


def test_profiles_verified_requires_pinned_generation_evidence():
    with pytest.raises(ValueError):
        profile(status=api().RecommendationStatus.VERIFIED)


def test_profiles_verified_input_does_not_override_missing_host_runtime():
    result = evaluated(status=api().RecommendationStatus.VERIFIED,
                       backend_revision="fixture-revision", verification_evidence=("Synthetic generation record",))
    assert result.status == api().RecommendationStatus.UNKNOWN


def test_profiles_never_pool_gpu_vram():
    result = evaluated("multi_gpu", ready=True, min_gpu_vram_bytes=40 * GIB)
    assert result.status == api().RecommendationStatus.UNSUPPORTED
    assert any("per GPU" in reason for reason in result.reasons)


def test_profiles_count_individually_qualifying_gpus():
    assert evaluated("multi_gpu", ready=True, min_gpu_count=2).status == api().RecommendationStatus.EXPERIMENTAL
    assert evaluated(ready=True, min_gpu_count=2).status == api().RecommendationStatus.UNSUPPORTED


@pytest.mark.parametrize("fixture,word", [("unsupported_linux", "distribution"), ("arm64", "architecture"), ("no_gpu", "GPU"), ("insufficient_disk", "disk")])
def test_profiles_known_mismatch_is_unsupported_even_with_unknown_runtime(fixture, word):
    result = evaluated(fixture)
    assert result.status == api().RecommendationStatus.UNSUPPORTED
    assert any(word in reason for reason in result.reasons)


@pytest.mark.parametrize("fixture", ["missing_nvidia_smi", "missing_driver"])
def test_profiles_missing_driver_is_unknown_not_proven_incompatible(fixture):
    assert evaluated(fixture).status == api().RecommendationStatus.UNKNOWN


def test_profiles_unknown_hardware_is_not_supported():
    assert api().evaluate_profile(HardwareInfo(), profile()).status == api().RecommendationStatus.UNKNOWN


def test_profiles_ubuntu_identification_does_not_certify_all_versions():
    hw = replace(scan().hardware, distribution_version="99.04", cuda_available=True)
    result = api().evaluate_profile(hw, profile())
    assert result.status == api().RecommendationStatus.UNKNOWN
    assert any("version" in reason for reason in result.reasons)


def test_profiles_debian_requires_exact_evidence_too():
    hw = replace(scan().hardware, distribution="debian", distribution_version="12", cuda_available=True)
    assert api().evaluate_profile(hw, profile()).status == api().RecommendationStatus.EXPERIMENTAL


def test_profiles_known_insufficient_ram():
    assert evaluated(ready=True, min_ram_bytes=30 * GIB).status == api().RecommendationStatus.UNSUPPORTED


def test_profiles_disk_estimate_and_provenance_are_supplied_not_invented():
    result = evaluated(ready=True)
    assert result.disk.required_bytes == 10 * GIB
    assert result.disk.evidence == ("Synthetic disk estimate for policy testing",)
    assert evaluated(ready=True, disk=None).status == api().RecommendationStatus.UNKNOWN


@pytest.mark.parametrize("changes", [{"min_ram_bytes": None}, {"min_gpu_count": None}, {"min_gpu_vram_bytes": None}, {"gpu_names": ()}, {"architectures": ()}, {"distributions": ()}, {"driver_versions": ()}, {"service_managers": ()}, {"requires_cuda_runtime": None}])
def test_profiles_incomplete_requirements_stay_unknown(changes):
    assert evaluated(ready=True, **changes).status == api().RecommendationStatus.UNKNOWN


def test_profiles_unlisted_gpu_or_driver_not_promoted_to_verified():
    assert evaluated(ready=True, gpu_names=("Other GPU",)).status == api().RecommendationStatus.UNKNOWN
    assert evaluated(ready=True, driver_versions=("999.1",)).status == api().RecommendationStatus.UNKNOWN


def test_profiles_measured_unusable_runtime_is_unsupported():
    hw = replace(scan().hardware, cuda_available=False)
    assert api().evaluate_profile(hw, profile()).status == api().RecommendationStatus.UNSUPPORTED


def test_profiles_explicit_cpu_profile_does_not_require_gpu_or_cuda():
    hw = replace(scan("no_gpu").hardware, cuda_available=None)
    result = api().evaluate_profile(hw, profile(min_gpu_count=0, min_gpu_vram_bytes=0, gpu_names=(), requires_cuda_runtime=False, driver_versions=()))
    assert result.status == api().RecommendationStatus.EXPERIMENTAL


def test_profiles_recommendations_preserve_input_order():
    p = api()
    results = p.recommend_profiles(scan().hardware, (profile(name="first"), profile(name="second")))
    assert [result.profile_name for result in results] == ["first", "second"]


@pytest.mark.parametrize("bad", [-1, float("inf"), float("nan"), True, 1.5])
def test_profiles_reject_invalid_estimates(bad):
    with pytest.raises(ValueError):
        api().DiskEstimate(bad, ("source",))
    with pytest.raises(ValueError):
        profile(min_ram_bytes=bad)
    with pytest.raises(ValueError):
        profile(min_gpu_count=bad)
    with pytest.raises(ValueError):
        profile(min_gpu_vram_bytes=bad)


@pytest.mark.parametrize("evidence", [(), ("",), (" ",), "not-a-tuple"])
def test_profiles_require_nonempty_provenance(evidence):
    with pytest.raises(ValueError):
        api().DiskEstimate(123, evidence)
    with pytest.raises(ValueError):
        profile(evidence=evidence)


@pytest.mark.parametrize("changes", [
    {"verification_evidence": "not-a-tuple"},
    {"verification_evidence": ("",)},
    {"backend_revision": 123},
    {"backend_revision": " "},
    {"min_gpu_count": 0, "min_gpu_vram_bytes": GIB},
])
def test_profiles_reject_malformed_or_contradictory_optional_evidence(changes):
    with pytest.raises(ValueError):
        profile(**changes)


@pytest.mark.parametrize("pci", [
    {"returncode": None, "error": "not-found"},
    {"stdout": "malformed PCI output\n"},
], ids=["missing-pci", "malformed-pci"])
@pytest.mark.parametrize("requirements", [
    {"min_gpu_count": 2},
    {"min_gpu_vram_bytes": 40 * GIB},
], ids=["insufficient-observed-count", "insufficient-observed-vram"])
def test_profiles_partial_gpu_inventory_cannot_prove_insufficient_hardware(pci, requirements):
    hw = replace(scan(overrides={"pci": pci}).hardware, cuda_available=True)
    assert len(hw.gpus) == 1
    assert hw.gpus[0].vram_bytes == 24 * GIB
    result = api().evaluate_profile(hw, profile(**requirements))
    assert result.status == api().RecommendationStatus.UNKNOWN
    assert any("inventory" in reason.lower() for reason in result.reasons)


@pytest.mark.parametrize("fixture,requirements", [
    ("rtx4090", {"min_gpu_count": 2}),
    ("multi_gpu", {"min_gpu_vram_bytes": 40 * GIB}),
    ("no_gpu", {"min_gpu_count": 1}),
])
def test_profiles_complete_gpu_inventory_can_prove_insufficient_hardware(fixture, requirements):
    assert evaluated(fixture, ready=True, **requirements).status == api().RecommendationStatus.UNSUPPORTED


def test_profiles_sufficient_observed_gpus_do_not_require_complete_inventory():
    hw = replace(scan(overrides={"pci": {"returncode": None, "error": "not-found"}}).hardware,
                 cuda_available=True)
    assert api().evaluate_profile(hw, profile()).status == api().RecommendationStatus.EXPERIMENTAL


def test_profiles_partial_inventory_does_not_pool_observed_vram():
    hw = replace(scan("multi_gpu", {"pci": {"returncode": None, "error": "not-found"}}).hardware,
                 cuda_available=True)
    result = api().evaluate_profile(hw, profile(min_gpu_vram_bytes=40 * GIB))
    assert result.status == api().RecommendationStatus.UNKNOWN
    assert not any("enough individual devices" in reason for reason in result.reasons)


@pytest.mark.parametrize("gpus", [(), None, (GPUInfo("NVIDIA GeForce RTX 4090", 24 * GIB),)],
                         ids=["empty", "unmeasured", "one-observed-gpu"])
def test_profiles_hand_built_gpu_inventory_defaults_conservatively(gpus):
    result = api().evaluate_profile(HardwareInfo(gpus=gpus), profile(min_gpu_count=2))
    assert result.status == api().RecommendationStatus.UNKNOWN

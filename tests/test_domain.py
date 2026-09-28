"""Domain contracts, independent of any inference backend."""

import math

import pytest


def test_hardware_preserves_unknown_and_measured_absence():
    from h3server.domain import GPUInfo, HardwareInfo

    unknown = HardwareInfo()
    assert unknown.gpus is None
    assert unknown.ram_bytes is None
    assert unknown.cuda_available is None
    assert unknown.service_manager is None
    hardware = HardwareInfo(
        distribution="Debian", distribution_version="13", architecture="x86_64",
        cpu="Generic CPU", cpu_count=8, ram_bytes=32 * 1024**3,
        gpus=(GPUInfo(name="Generic GPU", vram_bytes=24 * 1024**3),),
        driver_version="example", cuda_available=False, disk_free_bytes=0,
        service_manager="systemd", evidence=("local scan",),
    )
    assert hardware.gpus[0].vram_bytes == 24 * 1024**3
    assert hardware.cuda_available is False
    assert HardwareInfo(gpus=(), gpu_inventory_complete=True).gpus == ()


@pytest.mark.parametrize("field", ["cpu_count", "ram_bytes", "disk_free_bytes"])
def test_hardware_rejects_negative_measurements(field):
    from h3server.domain import HardwareInfo

    with pytest.raises(ValueError):
        HardwareInfo(**{field: -1})


def test_gpu_rejects_negative_vram():
    from h3server.domain import GPUInfo

    with pytest.raises(ValueError):
        GPUInfo(name="GPU", vram_bytes=-1)


def test_event_unknown_progress_is_not_a_stage_percentage():
    from h3server.domain import Event, Progress, StageStatus

    event = Event(stage="download", status=StageStatus.RUNNING,
                  progress=Progress(completed=123, unit="bytes"))
    assert event.progress.percent is None
    assert event.progress.total is None
    finished = Event(stage="export", status=StageStatus.COMPLETED)
    assert finished.progress is None
    assert Event(stage="scan", status=StageStatus.RUNNING).progress is None


@pytest.mark.parametrize("completed,total,percent", [(0, 10, 0), (3, 10, 30), (10, 10, 100), (0, 0, None)])
def test_progress_percentage_is_derived_from_measurements(completed, total, percent):
    from h3server.domain import Progress

    assert Progress(completed=completed, total=total, unit="steps").percent == percent


@pytest.mark.parametrize("completed,total", [(-1, 10), (11, 10), (1, 0), (0, -1),
                                            (math.nan, 10), (math.inf, None),
                                            (0, math.inf), (0, math.nan)])
def test_progress_rejects_invalid_percentage_bounds(completed, total):
    from h3server.domain import Progress

    with pytest.raises(ValueError):
        Progress(completed=completed, total=total, unit="items")


def test_progress_requires_a_unit():
    from h3server.domain import Progress

    with pytest.raises(ValueError):
        Progress(completed=0, unit=" ")


@pytest.mark.parametrize("status", ["supported", "unsupported", "unknown"])
def test_capability_has_explicit_status_and_evidence(status):
    from h3server.domain import Capability, CapabilityStatus

    capability = Capability(status=CapabilityStatus(status), evidence=("pinned backend probe",))
    assert capability.status.value == status
    assert capability.evidence == ("pinned backend probe",)
    with pytest.raises(ValueError):
        Capability(status=CapabilityStatus(status), evidence=())
    with pytest.raises(ValueError):
        Capability(status=CapabilityStatus(status), evidence=(" ",))


def test_capabilities_default_to_unknown_not_unsupported():
    from h3server.domain import Capabilities, CapabilityStatus, JobRequest

    capabilities = Capabilities()
    assert capabilities.lora.status is CapabilityStatus.UNKNOWN
    assert capabilities.sparse_attention.status is CapabilityStatus.UNKNOWN
    assert capabilities.lora.evidence
    JobRequest(prompt="A landscape").validate_capabilities(capabilities)


@pytest.mark.parametrize("status", ["unknown", "unsupported"])
@pytest.mark.parametrize("options,feature", [({"loras": ("adapter-a",)}, "lora"),
                                             ({"sparse_attention": True}, "sparse_attention"),
                                             ({"sparse_attention": False}, "sparse_attention")])
def test_job_request_rejects_unavailable_explicit_options(status, options, feature):
    from h3server.domain import Capabilities, Capability, CapabilityStatus, JobRequest

    capability = Capability(status=CapabilityStatus(status), evidence=("backend probe",))
    capabilities = Capabilities(lora=capability, sparse_attention=capability)
    with pytest.raises(ValueError, match=feature):
        JobRequest(prompt="A landscape", **options).validate_capabilities(capabilities)


def test_job_request_retains_supported_options():
    from h3server.domain import Capabilities, Capability, CapabilityStatus, JobRequest

    supported = Capability(status=CapabilityStatus.SUPPORTED, evidence=("verified runtime",))
    request = JobRequest(prompt="A landscape", loras=("adapter-a",), sparse_attention=True)
    request.validate_capabilities(Capabilities(lora=supported, sparse_attention=supported))
    assert request.loras == ("adapter-a",)
    assert request.sparse_attention is True


@pytest.mark.parametrize("options", [{"prompt": " "}, {"prompt": "x", "loras": ("",)}])
def test_job_request_rejects_empty_identifiers(options):
    from h3server.domain import JobRequest

    with pytest.raises(ValueError):
        JobRequest(**options)


def test_job_keeps_lifecycle_separate_from_event_progress():
    from h3server.domain import Job, JobRequest, JobStatus

    job = Job(job_id="job-123", request=JobRequest(prompt="A landscape"))
    assert job.status is JobStatus.QUEUED
    assert Job(job_id="job-123", request=job.request, status=JobStatus.INTERRUPTED).status.value == "interrupted"


def test_capability_cannot_claim_support_with_default_evidence():
    from h3server.domain import Capability, CapabilityStatus

    with pytest.raises(TypeError):
        Capability(status=CapabilityStatus.SUPPORTED)


@pytest.mark.parametrize("evidence", ["probe", ["probe"], (None,)])
def test_capability_requires_immutable_text_evidence(evidence):
    from h3server.domain import Capability, CapabilityStatus

    with pytest.raises(ValueError):
        Capability(status=CapabilityStatus.SUPPORTED, evidence=evidence)


@pytest.mark.parametrize("options", [{"loras": "adapter"}, {"loras": ["adapter"]},
                                     {"loras": (None,)}, {"sparse_attention": "false"},
                                     {"sparse_attention": 0}])
def test_job_options_reject_malformed_types_instead_of_coercing(options):
    from h3server.domain import JobRequest

    with pytest.raises(ValueError):
        JobRequest(prompt="A landscape", **options)


@pytest.mark.parametrize("completed", [True, "1"])
def test_progress_requires_numeric_measurements_not_flags_or_strings(completed):
    from h3server.domain import Progress

    with pytest.raises(ValueError):
        Progress(completed=completed, total=10, unit="steps")


def test_hardware_gpu_inventory_completeness_requires_explicit_evidence():
    from h3server.domain import GPUInfo, HardwareInfo

    for gpus in (None, (), (GPUInfo("Generic GPU", 24 * 1024**3),)):
        assert HardwareInfo(gpus=gpus).gpu_inventory_complete is False
    assert HardwareInfo(gpus=(), gpu_inventory_complete=True).gpu_inventory_complete is True


@pytest.mark.parametrize("value", [None, 0, 1, "true"])
def test_hardware_gpu_inventory_completeness_rejects_non_boolean(value):
    from h3server.domain import HardwareInfo

    with pytest.raises(ValueError):
        HardwareInfo(gpus=(), gpu_inventory_complete=value)


def test_hardware_complete_gpu_inventory_requires_observations():
    from h3server.domain import HardwareInfo

    with pytest.raises(ValueError):
        HardwareInfo(gpu_inventory_complete=True)

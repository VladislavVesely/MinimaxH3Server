"""All scans are fixture-driven; never inspect the test runner's hardware."""

import importlib
import json
from pathlib import Path
import subprocess

import pytest


FIXTURES = Path(__file__).parent / "fixtures" / "hardware"
GIB = 1024**3


def api():
    # Assertion failure, rather than collection failure, makes the initial red explicit.
    assert importlib.util.find_spec("h3server.probe") is not None, "probe implementation missing"
    return importlib.import_module("h3server.probe")


def scan(name="rtx4090", overrides=None, **kwargs):
    probe = api()
    data = json.loads((FIXTURES / f"{name}.json").read_text())["commands"]
    data.update(overrides or {})
    seen = []
    command_names = {argv: key for key, argv in probe.PROBE_COMMANDS.items()}

    def runner(argv, *, timeout):
        assert isinstance(argv, tuple)
        assert 0 < timeout <= 30
        key = "disk" if argv[:3] == ("df", "-Pk", "--") else command_names[argv]
        seen.append(key)
        return probe.CommandResult(**data[key])

    result = probe.probe_hardware(runner, **kwargs)
    assert len(seen) == len(data)
    assert len(set(seen)) == len(seen)
    return result


def test_probe_4090_is_an_observation_not_a_default():
    result = scan()
    hw = result.hardware
    assert (hw.distribution, hw.distribution_version, hw.architecture) == ("ubuntu", "24.04", "x86_64")
    assert (hw.cpu, hw.cpu_count, hw.ram_bytes) == ("Fixture CPU", 2, 29 * GIB)
    assert [(gpu.name, gpu.vram_bytes) for gpu in hw.gpus] == [("NVIDIA GeForce RTX 4090", 24 * GIB)]
    assert hw.driver_version == "550.54.14"
    assert hw.disk_free_bytes == 400 * GIB
    assert hw.service_manager == "systemd"
    assert result.kernel == "Linux"
    assert result.disk_path == "/"
    assert hw.evidence


def test_probe_multi_gpu_preserves_individual_memory():
    assert [gpu.vram_bytes for gpu in scan("multi_gpu").hardware.gpus] == [24 * GIB, 24 * GIB]


@pytest.mark.parametrize("fixture", ["missing_nvidia_smi", "missing_driver"])
def test_probe_pci_identity_survives_missing_tool_or_driver(fixture):
    result = scan(fixture)
    assert len(result.hardware.gpus) == 1
    assert "4090" in result.hardware.gpus[0].name
    assert result.hardware.gpus[0].vram_bytes is None
    assert result.hardware.driver_version is None
    assert result.hardware.cuda_available is None
    assert any(record.result.stderr for record in result.commands)
    assert not any("couldn't communicate" in item for item in result.hardware.evidence)


def test_probe_missing_smi_and_pci_is_unknown_not_no_gpu():
    result = scan("missing_nvidia_smi", {"pci": {"returncode": None, "error": "not-found"}})
    assert result.hardware.gpus is None


def test_probe_measured_no_gpu_requires_pci_evidence():
    assert scan("no_gpu").hardware.gpus == ()


def test_probe_cuda_compatibility_is_not_toolkit_or_runtime():
    result = scan()
    assert result.cuda.driver_compatibility_version == "12.4"
    assert result.cuda.toolkit_version is None
    assert result.cuda.runtime_usable is None
    assert result.hardware.cuda_available is None
    result = scan(overrides={"cuda_toolkit": {"stdout": "Cuda compilation tools, release 12.3, V12.3.107\n"}})
    assert result.cuda.toolkit_version == "12.3"
    assert result.cuda.runtime_usable is None


def test_probe_arm64():
    hw = scan("arm64").hardware
    assert hw.architecture == "aarch64"
    assert hw.cpu == "Fixture ARM CPU"
    assert hw.cpu_count == 1


def test_probe_unsupported_linux_is_still_an_observation():
    assert scan("unsupported_linux").hardware.distribution == "fedora"


def test_probe_disk_records_target_and_available_not_total():
    result = scan("insufficient_disk", disk_path="/srv/models")
    assert result.hardware.disk_free_bytes == 1000 * 1024
    assert result.disk_path == "/srv/models"
    assert next(record.argv for record in result.commands if record.name == "disk") == ("df", "-Pk", "--", "/srv/models")


@pytest.mark.parametrize("name,field", [("os_release", "distribution"), ("architecture", "architecture"), ("cpu", "cpu_count"), ("memory", "ram_bytes"), ("disk", "disk_free_bytes"), ("service_manager", "service_manager")])
@pytest.mark.parametrize("output", ["", "garbled\n", "-1\n"])
def test_probe_malformed_output_is_unknown(name, field, output):
    result = scan(overrides={name: {"stdout": output}})
    assert getattr(result.hardware, field) is None
    assert any(name in issue for issue in result.issues)


def test_probe_failed_stdout_not_parsed_as_success():
    result = scan(overrides={"memory": {"returncode": 1, "stdout": "MemTotal: 12345 kB", "stderr": "fixture error"}})
    assert result.hardware.ram_bytes is None
    assert "fixture error" not in " ".join(result.hardware.evidence)


def test_probe_malformed_gpu_data_falls_back_to_pci():
    result = scan(overrides={"nvidia_gpus": {"stdout": "00000000:01:00.0, NVIDIA GeForce RTX 4090, N/A, 550.54.14\n"}})
    assert result.hardware.gpus[0].vram_bytes is None
    assert any("nvidia_gpus" in issue for issue in result.issues)


@pytest.mark.parametrize("text", ["", "garbled", "0000:00:00.0 invalid line"])
def test_probe_invalid_pci_does_not_measure_absence(text):
    assert scan("missing_nvidia_smi", {"pci": {"stdout": text}}).hardware.gpus is None


def test_probe_preserves_non_nvidia_pci_gpu():
    result = scan(overrides={"pci": {"stdout": "0000:01:00.0 VGA compatible controller [0300]: NVIDIA [10de:2684]\n0000:03:00.0 VGA compatible controller [0300]: AMD Radeon [1002:1234]\n"}})
    assert len(result.hardware.gpus) == 2
    assert "AMD" in result.hardware.gpus[1].name
    assert result.hardware.gpus[1].vram_bytes is None


def test_os_release_is_data_never_shell_code(tmp_path):
    marker = tmp_path / "must-not-exist"
    result = scan(overrides={"os_release": {"stdout": f'ID=ubuntu\nVERSION_ID="$(touch {marker})"\nNAME="`touch {marker}`"\n'}})
    assert not marker.exists()
    assert result.hardware.distribution == "ubuntu"
    assert result.hardware.distribution_version is None


@pytest.mark.parametrize("text", ['ID="ubuntu\nVERSION_ID=24.04\n', 'ID=ubuntu\nID=debian\nVERSION_ID=24.04\n', 'ID=ubuntu extra\nVERSION_ID=24.04\n'])
def test_os_release_malformed_or_duplicate_id_is_unknown(text):
    assert scan(overrides={"os_release": {"stdout": text}}).hardware.distribution is None


@pytest.mark.parametrize("timeout", [0, -1, 31, float("inf"), float("nan"), True])
def test_probe_rejects_unbounded_timeout(timeout):
    with pytest.raises(ValueError):
        api().probe_hardware(lambda *args, **kwargs: pytest.fail("must not run"), timeout=timeout)


@pytest.mark.parametrize("path", ["relative", "-flag", "/tmp\x00bad", "/tmp\nwrong"])
def test_probe_rejects_invalid_disk_path(path):
    with pytest.raises(ValueError):
        api().probe_hardware(lambda *args, **kwargs: pytest.fail("must not run"), disk_path=path)


def test_local_runner_uses_argv_no_shell_and_fixed_locale(monkeypatch):
    def fake_run(argv, **kwargs):
        assert argv == ("uname", "-m")
        assert kwargs["shell"] is False
        assert kwargs["timeout"] == 5
        assert kwargs["env"]["LC_ALL"] == "C"
        assert kwargs["stdin"] is subprocess.DEVNULL
        return subprocess.CompletedProcess(argv, 0, "x86_64\n", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = api().run_local(("uname", "-m"), timeout=5)
    assert result.stdout == "x86_64\n"
    assert result.error is None


@pytest.mark.parametrize("exception,error", [(FileNotFoundError("fixture"), "not-found"), (PermissionError("fixture"), "os-error"), (subprocess.TimeoutExpired(("uname",), 1, output=b"partial", stderr=b"slow"), "timeout")])
def test_local_runner_errors_are_structured(monkeypatch, exception, error):
    def fake_run(*args, **kwargs):
        raise exception

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = api().run_local(("uname", "-m"), timeout=1)
    assert result.returncode is None
    assert result.error == error
    if error == "timeout":
        assert result.stdout == "partial"
        assert result.stderr == "slow"


@pytest.mark.parametrize("name,output,field", [
    ("memory", "MemTotal: 1024 kB\nMemTotal: garbage\n", "ram_bytes"),
    ("memory", "MemTotal: " + "9" * 5000 + " kB\n", "ram_bytes"),
    ("disk", "Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/sda " + "9" * 5000 + " 0 100 0% /\n", "disk_free_bytes"),
], ids=["duplicate-memory", "oversized-memory", "oversized-disk"])
def test_probe_ambiguous_or_oversized_numbers_fail_conservatively(name, output, field):
    assert getattr(scan(overrides={name: {"stdout": output}}).hardware, field) is None


def test_probe_oversized_vram_falls_back_without_crashing():
    output = "00000000:01:00.0, NVIDIA GeForce RTX 4090, " + "9" * 5000 + ", 550.54.14\n"
    assert scan(overrides={"nvidia_gpus": {"stdout": output}}).hardware.gpus[0].vram_bytes is None


def test_probe_pci_without_numeric_device_id_does_not_measure_absence():
    assert scan("missing_nvidia_smi", {"pci": {"stdout": "0000:00:00.0 Host bridge [0600]: garbage\n"}}).hardware.gpus is None


@pytest.mark.parametrize("argv", [("sh", "-c", "true"), ("nvidia-smi", "-pm", "1"), ("df", "-Pk", "--", "relative")])
def test_local_runner_rejects_commands_outside_read_only_probe(monkeypatch, argv):
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("must not execute"))
    with pytest.raises(ValueError):
        api().run_local(argv, timeout=1)

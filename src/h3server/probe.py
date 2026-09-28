"""Read-only Linux observations through an explicit, injectable argv runner.

No command establishes usable CUDA or model feasibility. The local runner is
opt-in; a future SSH runner must preserve argv semantics without shell expansion.
"""

import csv
from dataclasses import dataclass
import math
import os
import re
import shlex
import subprocess
from types import MappingProxyType
from typing import Protocol

from h3server.domain import GPUInfo, HardwareInfo


PROBE_COMMANDS = MappingProxyType({
    "kernel": ("uname", "-s"),
    "os_release": ("cat", "/etc/os-release"),
    "architecture": ("uname", "-m"),
    "cpu": ("cat", "/proc/cpuinfo"),
    "memory": ("cat", "/proc/meminfo"),
    "pci": ("lspci", "-Dnn"),
    "nvidia_gpus": ("nvidia-smi", "--query-gpu=pci.bus_id,name,memory.total,driver_version", "--format=csv,noheader,nounits"),
    "nvidia_status": ("nvidia-smi",),
    "cuda_toolkit": ("nvcc", "--version"),
    "service_manager": ("cat", "/proc/1/comm"),
})


@dataclass(frozen=True)
class CommandResult:
    stdout: str = ""
    stderr: str = ""
    returncode: int | None = 0
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.returncode == 0 and self.error is None


class CommandRunner(Protocol):
    def __call__(self, argv: tuple[str, ...], *, timeout: float) -> CommandResult: ...


@dataclass(frozen=True)
class CommandRecord:
    name: str
    argv: tuple[str, ...]
    result: CommandResult


@dataclass(frozen=True)
class CUDAEvidence:
    driver_compatibility_version: str | None = None
    toolkit_version: str | None = None
    runtime_usable: bool | None = None


@dataclass(frozen=True)
class ProbeResult:
    hardware: HardwareInfo
    kernel: str | None
    cuda: CUDAEvidence
    disk_path: str
    commands: tuple[CommandRecord, ...]
    issues: tuple[str, ...]


def _validate_timeout(timeout: float) -> None:
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 30:
        raise ValueError("timeout must be finite and in (0, 30] seconds")


def _text(value: str | bytes | None) -> str:
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""


def _validate_disk_path(disk_path: str) -> None:
    if not isinstance(disk_path, str) or not disk_path.startswith("/") or any(ord(c) < 32 for c in disk_path):
        raise ValueError("disk_path must be an absolute target path without control characters")


def run_local(argv: tuple[str, ...], *, timeout: float) -> CommandResult:
    """Execute one allowlisted probe command, with no shell or interactive input."""
    _validate_timeout(timeout)
    if not isinstance(argv, tuple):
        raise ValueError("probe commands must be argv tuples")
    if len(argv) == 4 and argv[:3] == ("df", "-Pk", "--"):
        _validate_disk_path(argv[3])
    elif argv not in PROBE_COMMANDS.values():
        raise ValueError("command is not a read-only probe command")
    try:
        completed = subprocess.run(
            argv, shell=False, stdin=subprocess.DEVNULL, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=timeout,
            env={**os.environ, "LC_ALL": "C"},
        )
    except subprocess.TimeoutExpired as exc:
        return CommandResult(_text(exc.stdout), _text(exc.stderr), None, "timeout")
    except FileNotFoundError as exc:
        return CommandResult(stderr=str(exc), returncode=None, error="not-found")
    except OSError as exc:
        return CommandResult(stderr=str(exc), returncode=None, error="os-error")
    return CommandResult(completed.stdout, completed.stderr, completed.returncode)


def _os_release(text: str) -> dict[str, str]:
    # shlex only decodes quoting. It never evaluates substitutions or runs code.
    values: dict[str, str] = {}
    seen: set[str] = set()
    for line in text.splitlines():
        match = re.fullmatch(r"\s*(ID|VERSION_ID)=(.*)", line)
        if not match:
            continue
        key, raw = match.groups()
        if key in seen:
            values.pop(key, None)
            continue
        seen.add(key)
        try:
            parts = shlex.split(raw, comments=False, posix=True)
        except ValueError:
            continue
        pattern = r"[a-z0-9._-]+" if key == "ID" else r"[A-Za-z0-9._-]+"
        if len(parts) == 1 and re.fullmatch(pattern, parts[0]):
            values[key] = parts[0]
    return values


def _pci_address(value: str) -> str | None:
    match = re.fullmatch(r"([0-9a-fA-F]{4,8}):([0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7])", value)
    if not match:
        return None
    return f"{int(match[1], 16):04x}:{match[2].lower()}"


def _pci_gpus(text: str) -> dict[str, GPUInfo] | None:
    if not text.strip():
        return None
    gpus = {}
    seen = set()
    for line in text.splitlines():
        match = re.fullmatch(r"(\S+) .+? \[([0-9a-fA-F]{4})\]: (.+)", line)
        if (not match or not (address := _pci_address(match[1])) or address in seen
                or not re.search(r"\[[0-9a-fA-F]{4}:[0-9a-fA-F]{4}\]", match[3])):
            return None
        seen.add(address)
        if match[2].lower().startswith("03"):
            gpus[address] = GPUInfo(name=match[3])
    return gpus


def _nvidia_gpus(text: str) -> tuple[dict[str, GPUInfo], str | None] | None:
    if not text.strip():
        return None
    gpus = {}
    drivers = set()
    try:
        for row in csv.reader(text.splitlines(), skipinitialspace=True, strict=True):
            if len(row) != 4:
                return None
            bus, name, memory, driver = (part.strip() for part in row)
            address = _pci_address(bus)
            if (not address or address in gpus or not name or not re.fullmatch(r"[0-9]{1,20}", memory)
                    or int(memory) <= 0 or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)+", driver)):
                return None
            gpus[address] = GPUInfo(name, int(memory) * 1024**2)
            drivers.add(driver)
    except csv.Error:
        return None
    return gpus, next(iter(drivers)) if len(drivers) == 1 else None


def _cpu(text: str) -> tuple[str | None, int | None]:
    fields: dict[str, list[str]] = {}
    for line in text.splitlines():
        key, separator, value = line.partition(":")
        if separator and value.strip():
            fields.setdefault(key.strip(), []).append(value.strip())
    names = fields.get("model name") or fields.get("Hardware") or fields.get("Processor")
    processors = fields.get("processor", [])
    count = (len(processors) if processors and all(re.fullmatch(r"[0-9]+", p) for p in processors)
             and len(set(processors)) == len(processors) else None)
    return "; ".join(dict.fromkeys(names)) if names else None, count


def _disk_free(text: str) -> int | None:
    lines = text.splitlines()
    if len(lines) != 2 or not lines[0].startswith("Filesystem"):
        return None
    parts = lines[1].split()
    if (len(parts) < 6 or not all(re.fullmatch(r"[0-9]{1,20}", part) for part in parts[1:4])
            or not re.fullmatch(r"[0-9]+%", parts[4]) or int(parts[3]) > int(parts[1])):
        return None
    return int(parts[3]) * 1024


def probe_hardware(runner: CommandRunner, *, disk_path: str = "/", timeout: float = 5) -> ProbeResult:
    """Collect facts, not installation permissions or inference readiness.

    disk_path must be an existing absolute path on the target filesystem; no
    directories are created. PID 1's name is only service-manager evidence, not
    proof that a system service can be installed or controlled.
    """
    _validate_timeout(timeout)
    _validate_disk_path(disk_path)
    records = []
    issues = []
    observations = []
    outputs = {}
    for name, argv in (*PROBE_COMMANDS.items(), ("disk", ("df", "-Pk", "--", disk_path))):
        result = runner(argv, timeout=timeout)
        records.append(CommandRecord(name, argv, result))
        outputs[name] = result.stdout if result.succeeded else ""
        if not result.succeeded:
            # Raw errors are available separately in commands, not reinterpreted.
            issues.append(f"{name}: command unavailable or failed; observation unknown")

    def observed(name: str, value: object) -> None:
        if value is None:
            issues.append(f"{name}: missing or malformed output; observation unknown")
        else:
            observations.append(f"{name}: {value}")

    kernel = outputs["kernel"].strip()
    kernel = kernel if re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", kernel) else None
    observed("kernel", kernel)
    os_release = _os_release(outputs["os_release"])
    distribution, version = os_release.get("ID"), os_release.get("VERSION_ID")
    observed("os_release", distribution)
    observed("os_release version", version)
    arch = outputs["architecture"].strip()
    arch = arch if arch in {"x86_64", "aarch64", "arm64", "i386", "i686", "armv7l", "ppc64le", "s390x", "riscv64"} else None
    observed("architecture", arch)
    cpu, cpu_count = _cpu(outputs["cpu"])
    observed("cpu", cpu)
    observed("cpu count", cpu_count)
    memory_lines = [line for line in outputs["memory"].splitlines() if line.startswith("MemTotal:")]
    memory_match = re.fullmatch(r"MemTotal:\s+([0-9]{1,20}) kB\s*", memory_lines[0]) if len(memory_lines) == 1 else None
    ram = int(memory_match[1]) * 1024 if memory_match and int(memory_match[1]) > 0 else None
    observed("memory", ram)

    pci = _pci_gpus(outputs["pci"])
    nvidia = _nvidia_gpus(outputs["nvidia_gpus"])
    observed("pci", tuple(pci.values()) if pci is not None else None)
    observed("nvidia_gpus", tuple(nvidia[0].values()) if nvidia else None)
    driver = nvidia[1] if nvidia else None
    # Match PCI addresses, never collapse multiple devices or sum their VRAM.
    combined = dict(pci or {})
    if nvidia:
        combined.update(nvidia[0])
    gpus = tuple(combined.values()) if pci is not None or nvidia is not None else None
    compatibility = re.findall(r"CUDA Version:\s*([0-9]+\.[0-9]+)\b", outputs["nvidia_status"])
    toolkit = re.findall(r"Cuda compilation tools, release ([0-9]+\.[0-9]+)\b", outputs["cuda_toolkit"])
    cuda = CUDAEvidence(compatibility[0] if len(compatibility) == 1 else None,
                        toolkit[0] if len(toolkit) == 1 else None)
    observed("driver-reported CUDA compatibility (not installed toolkit or runtime)", cuda.driver_compatibility_version)
    observed("cuda_toolkit compiler version (not runtime usability)", cuda.toolkit_version)
    observations.append("CUDA runtime usability: unknown; no runtime or inference test performed")
    disk = _disk_free(outputs["disk"])
    observed(f"disk available bytes at {disk_path}", disk)
    manager = outputs["service_manager"].strip()
    manager = manager if manager in {"systemd", "init", "openrc-init", "runit", "s6-svscan", "tini", "dumb-init"} else None
    observed("service_manager PID 1 (not service control certification)", manager)
    return ProbeResult(
        HardwareInfo(distribution=distribution, distribution_version=version, architecture=arch,
                     cpu=cpu, cpu_count=cpu_count, ram_bytes=ram, gpus=gpus,
                     driver_version=driver, cuda_available=None, disk_free_bytes=disk,
                     service_manager=manager, evidence=tuple(observations)),
        kernel, cuda, disk_path, tuple(records), tuple(issues),
    )

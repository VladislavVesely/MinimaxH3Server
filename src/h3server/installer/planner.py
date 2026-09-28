"""Digest-bound installer documents. Inspection is read-only and never uses a shell.

Supported OS means an explicit package-manager path, NOT certified H3 execution.
Pass an SSH argv adapter or ``inspect_local``; missing evidence blocks mutations.
"""
from dataclasses import asdict, dataclass
import re
import subprocess
import os

from h3server.backends import load_manifest
from h3server.probe import CommandResult, _os_release
from h3server.store import ApprovalRequired, canonical_plan_digest

SUPPORTED = {"ubuntu": ("24.04", "26.04"), "debian": ("12", "13")}
PACKAGE = r"[a-z0-9][a-z0-9+.-]*(?::amd64)?"
VERSION = r"[A-Za-z0-9.+:~_-]+"
BASE_PACKAGES = ("python3", "python3-venv", "git", "ca-certificates")


@dataclass(frozen=True)
class ReleaseArtifact:
    """Controller-verified wheel, staged under releases/<release_id>/<filename>.

    The caller must authenticate release provenance; this digest is rechecked on
    target, not a substitute for signature verification by the release channel.
    """
    release_id: str
    filename: str
    sha256: str

    def __post_init__(self):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", self.release_id):
            raise ValueError("invalid release id")
        if not re.fullmatch(r"h3server-[A-Za-z0-9_.+-]+-py3-none-any\.whl", self.filename):
            raise ValueError("expected a portable h3server pure Python release wheel")
        if not re.fullmatch(r"[a-f0-9]{64}", self.sha256):
            raise ValueError("invalid artifact digest")


@dataclass(frozen=True)
class Inspection:
    distribution: str | None
    version: str | None
    architecture: str | None
    python_ready: bool
    driver_working: bool
    nvidia_device: bool | None
    secure_boot: str
    sudo_available: bool
    selector_available: bool
    driver_package: str | None
    packages: tuple[str, ...]
    package_changes: tuple[str, ...]
    issues: tuple[str, ...]
    refresh_metadata: bool = False


@dataclass(frozen=True)
class Approval:
    plan_digest: str
    license_acknowledged: bool = False
    eligibility_confirmed: bool = False
    privileged: bool = False
    reboot: bool = False


def inspect_local(argv, *, timeout):
    """Use only as the explicit runner for inspect_prerequisites (not arbitrary RPC)."""
    try:
        r = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                           text=True, errors="replace", timeout=timeout,
                           env={**os.environ, "LC_ALL": "C"})
        return CommandResult(r.stdout, r.stderr, r.returncode)
    except (OSError, subprocess.TimeoutExpired) as e:
        return CommandResult(error=type(e).__name__, returncode=None)


def package_changes(text):
    """APT's measured simulated mutations; never infer progress from its prose."""
    changes = tuple(line.strip() for line in text.splitlines() if line.startswith(("Inst ", "Remv ")))
    if any(line.startswith("Remv ") for line in changes):
        raise ValueError("APT proposes package removals; administrator review required")
    return changes


def installed_version(text):
    match = re.fullmatch(r"install ok installed\t(" + VERSION + r")\s*", text)
    return match[1] if match else None


def inspect_prerequisites(run=inspect_local) -> Inspection:
    def cmd(*args):
        return run(tuple(args), timeout=30)

    os_release = _os_release(cmd("cat", "/etc/os-release").stdout)
    distro, version = os_release.get("ID"), os_release.get("VERSION_ID")
    arch = cmd("uname", "-m").stdout.strip()
    if version not in SUPPORTED.get(distro, ()) or arch != "x86_64":
        return Inspection(distro, version, arch, False, False, None, "unknown", False,
                          False, None, (), (), ("Unsupported platform; no generic APT fallback.",))
    python_ready = cmd("python3", "-c", "import sys, venv, ensurepip; assert sys.version_info >= (3, 11)").succeeded
    smi = cmd("nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader")
    working = smi.succeeded and bool(re.fullmatch(r"(?:\d+(?:\.\d+)+\s*)+", smi.stdout))
    pci = cmd("lspci", "-Dn")
    nvidia = True if working else (bool(re.search(r"\b03[0-9a-f]{2}: 10de:", pci.stdout, re.I)) if pci.succeeded else None)
    boot = cmd("mokutil", "--sb-state")
    boot_text = boot.stdout + boot.stderr
    secure_boot = "disabled" if (boot.succeeded and "SecureBoot disabled" in boot_text) or "EFI variables are not supported on this system" in boot_text else "enabled" if "SecureBoot enabled" in boot_text else "unknown"
    sudo = cmd("sudo", "-n", "true").succeeded
    issues = []
    missing = []
    for package in BASE_PACKAGES:
        result = cmd("dpkg-query", "-W", "-f=${Status}\t${Version}\n", package)
        if not result.succeeded or not installed_version(result.stdout):
            missing.append(package)
    if not python_ready and "python3-venv" not in missing:
        issues.append("Installed Python must provide Python >=3.11, venv and ensurepip; repair the distro Python before retrying.")
    selector_available = working
    driver = None
    if not working:
        selector_package = "ubuntu-drivers-common" if distro == "ubuntu" else "nvidia-detect"
        selector = cmd("ubuntu-drivers", "devices") if distro == "ubuntu" else cmd("nvidia-detect")
        selector_available = selector.error is None and selector.returncode not in (None, 127)
        if not selector_available:
            missing.append(selector_package)
            if nvidia is None:
                missing.append("pciutils")
        elif distro == "ubuntu":
            candidates = re.findall(r"^driver\s*:\s*(nvidia-driver-[0-9]+(?:-server)?(?:-open)?)\s+-\s+distro\s+non-free\s+recommended\s*$", selector.stdout, re.M)
            if len(set(candidates)) == 1:
                driver = candidates[0]
        else:
            # nvidia-detect matches the PCI device to Debian's supported driver.
            candidates = re.findall(r"^\s*(nvidia(?:-tesla-[a-z0-9]+)?-driver)\s*$", selector.stdout, re.M)
            if len(set(candidates)) == 1:
                driver = candidates[0]
        if selector_available and driver is None:
            issues.append("Distro selector did not identify one suitable packaged NVIDIA driver; manual hardware review required.")
        if boot.error is not None or boot.returncode in (None, 127):
            missing.append("mokutil")
            selector_available = False  # inspect Secure Boot after installing its tool
        if driver and python_ready and selector_available:
            missing.append(driver)
    pins = []
    refresh = False
    for package in dict.fromkeys(missing):
        policy = cmd("apt-cache", "policy", package)
        match = re.search(r"^\s*Candidate: (" + VERSION + r")\s*$", policy.stdout, re.M) if policy.succeeded else None
        if not match:
            if distro == "debian" and package.startswith("nvidia"):
                issues.append("Debian non-free/non-free-firmware repository prerequisite requires explicit administrator approval; sources are never modified. Enable the distro's documented components, then rescan.")
            else:
                refresh = True
            continue
        pins.append(f"{package}={match[1]}")
    changes = ()
    if pins and not refresh:
        simulated = cmd("apt-get", "-s", "--no-remove", "install", *pins)
        if not simulated.succeeded:
            issues.append("APT dependency simulation failed; repair package metadata/dependencies and rescan.")
        else:
            try:
                changes = package_changes(simulated.stdout)
            except ValueError as e:
                issues.append(str(e))
    if refresh:
        pins = []  # only update indexes, then show newly resolved changes for approval
    return Inspection(distro, version, arch, python_ready, working, nvidia, secure_boot,
                      sudo, selector_available, driver, tuple(pins), changes, tuple(issues), refresh)


def requirements():
    constraints = load_manifest()["candidate_environment"]["constraints"]
    return ["diffusers @ " + constraints["diffusers"]] + [name + spec for name, spec in constraints.items() if name != "diffusers"]


def build_plan(inspection: Inspection, *, profile: str, artifact: ReleaseArtifact) -> dict:
    manifest = load_manifest()
    if profile not in {p["name"] for p in manifest["profiles"]}:
        raise ValueError("unknown experimental profile")
    blockers = list(inspection.issues)
    if inspection.version not in SUPPORTED.get(inspection.distribution, ()) or inspection.architecture != "x86_64":
        blockers.append("Supported installer matrix: Ubuntu 24.04/26.04, Debian 12/13, x86_64 only; this is not runtime certification.")
    discovery = inspection.refresh_metadata or not inspection.python_ready or not inspection.selector_available
    if not inspection.driver_working and not discovery:
        if inspection.nvidia_device is not True:
            blockers.append("NVIDIA device presence is unconfirmed; do not install a guessed driver.")
        if not inspection.driver_package:
            blockers.append("No distro-validated suitable driver is known.")
        if inspection.secure_boot != "disabled":
            blockers.append("Secure Boot enabled or unknown: manual signed-driver/MOK enrollment review required; automatic installation is blocked, never disable Secure Boot.")
    privileged = bool(inspection.packages or inspection.refresh_metadata)
    if privileged and not inspection.sudo_available:
        blockers.append("Approved noninteractive sudo is unavailable; arrange scoped sudo authorization and rescan. Passwords are never stored or piped to sudo.")
    # Do not install a newly discovered driver during a prerequisite-only round.
    if discovery and inspection.driver_package and any(p.startswith(inspection.driver_package + "=") for p in inspection.packages):
        blockers.append("Python/selector prerequisites and driver changes must be resolved in separate approval rounds; rescan after prerequisite repair.")
    p = {
        "schema": 1, "purpose": "prerequisites" if discovery else "environment",
        "platform": {"distribution": inspection.distribution, "version": inspection.version, "architecture": inspection.architecture},
        "profile": profile, "profile_confidence": "experimental", "artifact": asdict(artifact),
        "backend_revision": manifest["backend"]["revision"], "model_revision": manifest["model"]["revision"],
        "license": manifest["license"], "requirements": requirements(),
        "packages": list(inspection.packages), "package_changes": list(inspection.package_changes),
        "refresh_metadata": inspection.refresh_metadata,
        "driver_action": "preserve" if inspection.driver_working else "discover" if discovery else "install",
        "driver_package": inspection.driver_package,
        "privileged_required": privileged,
        "reboot_required": not inspection.driver_working and not discovery,
        "blockers": blockers,
        "stages": ["system-packages", "replan"] if discovery else ["system-packages", "environment", "dependencies", "verify"],
        "installation_ready": False, "model_ready": False, "generation_tested": False,
        "changes": ["Install only the listed APT versions and simulated dependency changes; no repository edits or full CUDA toolkit.",
                    "On interruption, configure only pending dpkg packages named in the approved simulation; unrelated broken packages block recovery.",
                    "Prerequisite-only completion requires automatic reinspection and a NEW plan approval; it is not installation readiness.",
                    "Write versioned application environment, cache, temporary files and logs beneath AppPaths; preserve HOME.",
                    "Resolve candidate Python dependencies on target and record pip report/freeze; not a globally tested lock.",
                    "No service activation, model download or generation in this plan."],
    }
    validate_plan(p)
    return p


def validate_plan(plan):
    """Reject modified executable content even if someone approved its digest."""
    canonical_plan_digest(plan)
    try:
        ReleaseArtifact(**plan["artifact"])
    except (KeyError, TypeError) as e:
        raise ValueError("invalid release artifact") from e
    for name in ("refresh_metadata", "privileged_required", "reboot_required", "installation_ready", "model_ready", "generation_tested"):
        if type(plan.get(name)) is not bool:
            raise ValueError("plan flags must be boolean")
    for name in ("packages", "package_changes", "blockers", "changes"):
        if type(plan.get(name)) is not list or any(type(v) is not str for v in plan[name]):
            raise ValueError("plan lists must contain strings")
    if any(plan[name] for name in ("installation_ready", "model_ready", "generation_tested")):
        raise ValueError("a proposed plan cannot assert readiness")
    m = load_manifest()
    if plan.get("schema") != 1 or plan.get("requirements") != requirements() or plan.get("backend_revision") != m["backend"]["revision"] or plan.get("model_revision") != m["model"]["revision"] or plan.get("license") != m["license"]:
        raise ValueError("unsupported plan or changed pinned backend requirements")
    if plan.get("profile") not in {p["name"] for p in m["profiles"]}:
        raise ValueError("unknown profile")
    if plan.get("purpose") not in {"prerequisites", "environment"}:
        raise ValueError("unknown plan purpose")
    expected = ["system-packages", "replan"] if plan["purpose"] == "prerequisites" else ["system-packages", "environment", "dependencies", "verify"]
    if plan["stages"] != expected:
        raise ValueError("unknown stage sequence")
    for pin in plan["packages"]:
        if not re.fullmatch(PACKAGE + "=" + VERSION, pin):
            raise ValueError("invalid package pin")
        name = pin.split("=")[0]
        if name not in (*BASE_PACKAGES, "pciutils", "mokutil", "ubuntu-drivers-common", "nvidia-detect") and not re.fullmatch(r"nvidia-driver-[0-9]+(?:-server)?(?:-open)?|nvidia(?:-tesla-[a-z0-9]+)?-driver", name):
            raise ValueError("unrecognized system package")
    if package_changes("\n".join(plan["package_changes"])) != tuple(plan["package_changes"]):
        raise ValueError("invalid package changes")
    if plan["refresh_metadata"] and plan["packages"]:
        raise ValueError("metadata refresh requires a separate plan")
    if plan["privileged_required"] != bool(plan["packages"] or plan["refresh_metadata"]):
        raise ValueError("invalid privileged scope")
    if plan["driver_action"] not in {"preserve", "discover", "install"}:
        raise ValueError("unknown driver action")
    if plan["reboot_required"] != (plan["driver_action"] == "install"):
        raise ValueError("invalid reboot scope")
    if plan["driver_action"] == "preserve" and (any("nvidia" in p.split("=")[0] for p in plan["packages"]) or any("nvidia" in line.split()[1] for line in plan["package_changes"])):
        raise ValueError("working NVIDIA driver must be preserved, including transitive APT changes")
    if plan["driver_action"] == "install" and not any(p.startswith(str(plan["driver_package"]) + "=") for p in plan["packages"]) and not plan["blockers"]:
        raise ValueError("driver installation requires a pinned distro package")
    return plan


def validate_approval(plan, approval: Approval):
    if approval.plan_digest != canonical_plan_digest(plan):
        raise ApprovalRequired("approval refers to a stale plan")
    validate_plan(plan)
    if plan["blockers"]:
        raise ApprovalRequired("; ".join(plan["blockers"]))
    platform = plan["platform"]
    if platform["version"] not in SUPPORTED.get(platform["distribution"], ()) or platform["architecture"] != "x86_64":
        raise ApprovalRequired("unsupported platform")
    if approval.license_acknowledged is not True or approval.eligibility_confirmed is not True:
        raise ApprovalRequired("license acknowledgement and eligibility confirmation required; full pinned license must be presented by caller")
    if plan["privileged_required"] and approval.privileged is not True:
        raise ApprovalRequired("separate privileged consent required")
    # Reboot is deliberately not implied here. Caller may approve it later against
    # the same digest; Installer stops at WAITING_FOR_REBOOT regardless.


def validate_reboot_approval(plan, approval: Approval):
    """Consent gate for the next component's reboot IO; never performs a reboot."""
    validate_approval(plan, approval)
    if not plan["reboot_required"] or approval.reboot is not True:
        raise ApprovalRequired("separate reboot consent for this plan is required")

"""Read-only facts for a local macOS controller; no CUDA/install claim."""
import os
import platform
import shutil
import subprocess
from h3server.domain import HardwareInfo
from h3server.probe import ProbeResult,CUDAEvidence


def mac_probe(home):
    def text(argv):
        try:
            result=subprocess.run(argv,stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=5,
                env={'PATH':'/usr/sbin:/usr/bin:/bin','LC_ALL':'C'})
            return result.stdout.strip() if result.returncode==0 else None
        except (OSError,subprocess.TimeoutExpired):return None
    cpu=text(('sysctl','-n','machdep.cpu.brand_string'))
    memory=text(('sysctl','-n','hw.memsize'))
    return ProbeResult(HardwareInfo(distribution='macos',distribution_version=platform.mac_ver()[0],
        architecture=platform.machine(),cpu=cpu,cpu_count=os.cpu_count(),
        ram_bytes=int(memory) if memory and memory.isdigit() else None,gpus=None,driver_version=None,
        cuda_available=None,disk_free_bytes=shutil.disk_usage(home).free,service_manager=None,
        evidence=('Local macOS observations; this H3 installer requires supported Linux/NVIDIA hardware.',),gpu_inventory_complete=False),
        'Darwin',CUDAEvidence(None,None),home,(),('Local installation is unsupported on macOS. Scan only; no changes made.',))

#!/usr/bin/env python3
"""Read-only OpenShell gateway qualification; never changes the host."""
import ctypes
import json
import platform
import re
import shutil
import subprocess

def kernel_tuple(release):
    match = re.match(r"^(\d+)\.(\d+)", release)
    return tuple(map(int, match.groups())) if match else (0, 0)

def parse_version(value):
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", value)
    return tuple(int(part or "0") for part in match.groups()) if match else (0, 0, 0)

def landlock_abi():
    """Read-only sys_landlock_create_ruleset(NULL, 0, VERSION); x86_64 / aarch64."""
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        version = libc.syscall(444, ctypes.c_void_p(0), ctypes.c_size_t(0), ctypes.c_uint(1))
        return version if version >= 0 else None
    except (AttributeError, OSError):
        return None

def run(argv):
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=6, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None

def probe(release=None, arch=None, landlock_probe=landlock_abi):
    release = release or platform.release()
    arch = arch or platform.machine()
    info = {"kernel": release, "architecture": arch, "landlock_abi": None,
            "docker_version": None, "docker_reachable": False, "openshell_cli": False,
            "blocking": [], "notes": []}
    if arch not in ("aarch64", "x86_64", "arm64", "amd64"):
        info["blocking"].append("Unsupported gateway architecture")
    if kernel_tuple(release) < (6, 2):
        info["blocking"].append("Kernel predates upstream Landlock ABI 3; do not deploy OpenShell here")
    abi = landlock_probe()
    info["landlock_abi"] = abi
    if abi is None:
        info["blocking"].append("Cannot confirm active Landlock ABI 3 or later")
    elif abi < 3:
        info["blocking"].append("Landlock ABI below 3")
    if not shutil.which("docker"):
        info["blocking"].append("Docker not installed (requires Engine 28+)")
    else:
        version = run(["docker", "version", "--format", "{{.Server.Version}}"])
        if version and version.returncode == 0:
            info["docker_reachable"] = True
            info["docker_version"] = version.stdout.strip()
            if parse_version(info["docker_version"]) < (28, 0, 0):
                info["blocking"].append("Docker Engine below version 28")
        else:
            info["blocking"].append("Docker daemon not accessible to current user")
    info["openshell_cli"] = bool(shutil.which("openshell"))
    if not info["openshell_cli"]:
        info["notes"].append("OpenShell CLI not installed; install after prerequisites pass")
    info["notes"].append("Sandbox startup still requires active seccomp, memory, and socket probes")
    info["ready_for_install"] = not info["blocking"]
    return info

if __name__ == "__main__":
    result = probe()
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["ready_for_install"] else 2)

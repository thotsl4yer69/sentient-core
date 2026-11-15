[CmdletBinding()]
param(
    [ValidateSet('192.168.1.160', '100.120.166.31')]
    [string]$Target = '192.168.1.160'
)
$ErrorActionPreference = 'Stop'
$keyPath = Join-Path ([Environment]::GetFolderPath('UserProfile')) '.ssh\orin_backup_ed25519'
$hostsPath = Join-Path ([Environment]::GetFolderPath('UserProfile')) '.ssh\known_hosts'
if (-not (Test-Path -LiteralPath $keyPath)) { throw "Existing Orin SSH key is missing: $keyPath" }
if (-not (Test-Path -LiteralPath $hostsPath)) { throw "Existing SSH host records are missing: $hostsPath" }
if (-not (Get-Command ssh -ErrorAction SilentlyContinue)) { throw 'Windows OpenSSH is not installed.' }
$sshArgs = @('-i', $keyPath, '-o', "UserKnownHostsFile=$hostsPath", '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=10')
$remoteScript = @'
from pathlib import Path
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

COMMIT = "228a212e75e92a24e08df080a25f8442715b31f9"
EXPECTED = {'audio_devices.py': '005a53ac3ca966fb0a206fcef61c72b4c8f56ff903a51bd08ad3951d070bd30d', 'handsfree.py': '89b5b4143b705bdf0f4030e3d2d454c6aa99ad5e84394f5295778e5d047f9f51', 'handsfree-controls.js': '9a04ee34db68cb820ca029dc04f16134c0e7d52347a1094c5eb3a15652316b47', 'install.py': '8f457f028add4cdc8cb07320ae73228cffa79bc9a02fe0e5c6f84e1dfe98fcdf', 'manifest.json': 'b70e0218f2a6ccaad5fc647ec327be792710b3be3407d62ece027957763cdb2d'}
if socket.gethostname() != "nano-desktop":
    raise SystemExit("Refusing to deploy to an unexpected host")
if not Path("/mnt/orin-ai/orin/voice/handsfree.py").is_file():
    raise SystemExit("The expected native Orin runtime is missing")
release = Path("/mnt/orin-ai/releases/native-voice-usb-" + COMMIT[:12])
release.mkdir(parents=True, exist_ok=True)
base = "https://raw.githubusercontent.com/thotsl4yer69/sentient-core/" + COMMIT + "/deploy/orin-native-voice/"
for name, expected in EXPECTED.items():
    target = release / name
    if target.is_symlink():
        raise SystemExit("Refusing to replace a release symlink: " + name)
    if target.exists():
        if hashlib.sha256(target.read_bytes()).hexdigest() != expected:
            raise SystemExit("Existing release file differs: " + name)
        continue
    with urllib.request.urlopen(base + name, timeout=20) as response:
        content = response.read(1024 * 1024)
    if hashlib.sha256(content).hexdigest() != expected:
        raise SystemExit("Download checksum mismatch: " + name)
    fd, temporary = tempfile.mkstemp(prefix="stage-", dir=release)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
print("Pinned deployment package verified", flush=True)
installer = str(release / "install.py")
subprocess.run([sys.executable, installer, "preflight"], check=True, timeout=30)
subprocess.run([sys.executable, installer, "apply"], check=True, timeout=180)
subprocess.run(["systemctl", "is-active", "orin-voice"], check=True, timeout=10)
status = json.loads(Path("/mnt/orin-ai/run/handsfree/status.json").read_text())
if time.time() - status.get("updated_at", 0) > 10:
    raise SystemExit("Voice status is stale; live health is not verified")
print(json.dumps({key: status.get(key) for key in
    ("phase", "wake_phrase", "source", "sink", "input_signal", "error")}, indent=2))
print("Deployment verified. Physical microphone acceptance remains a separate check.")

'@
$logPath = Join-Path $PSScriptRoot ('orin-voice-deploy-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')
$remoteScript | & ssh @sshArgs "nano@$Target" 'python3 -' | Tee-Object -FilePath $logPath
$sshExit = $LASTEXITCODE
if ($sshExit -ne 0) { throw "Deployment stopped (SSH exit $sshExit). See $logPath" }
Write-Host "Deployment receipt and voice status saved to $logPath"

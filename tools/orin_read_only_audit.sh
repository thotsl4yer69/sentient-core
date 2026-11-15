#!/usr/bin/env bash
# MAZLABZ Orin Nano — read-only baseline collector (reviewed plan companion).
# No sudo, installs, updates, service restarts or configuration changes.
# Writes an audit folder ONLY in the directory from which it is invoked.
set -u
umask 077
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
out="${PWD}/orin_read_only_audit_${stamp}"
mkdir -m 700 "$out" || exit 1

section() {
  local name="$1"; shift
  printf '[audit] %-24s' "$name"
  { printf '$'; printf ' %q' "$@"; printf '\n\n'; "$@"; } >"${out}/${name}.txt" 2>&1
  printf ' complete\n'
}

section identity bash -c 'printf "UTC: "; date -u --iso-8601=seconds; printf "Host: "; hostname; printf "Kernel: "; uname -a; printf "Device model: "; tr -d "\000" </proc/device-tree/model 2>/dev/null || true; printf "\nOS:\n"; cat /etc/os-release 2>/dev/null || true; printf "L4T:\n"; cat /etc/nv_tegra_release 2>/dev/null || true; printf "User: "; id'
section network bash -c 'ip -4 -br addr 2>/dev/null; echo; ip route 2>/dev/null; echo; ip -br link 2>/dev/null; echo; ss -ltnp 2>/dev/null'
section compute bash -c 'free -h; echo; df -hT; echo; lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINTS,MODEL; echo; uptime; echo; if command -v nvpmodel >/dev/null 2>&1; then nvpmodel -q; fi; if command -v tegrastats >/dev/null 2>&1; then timeout 4 tegrastats --interval 1000 || true; fi'
section units bash -c "systemctl list-units --all --no-pager --plain --type=service 2>/dev/null | grep -Ei 'airi|sentient|cortana|avatar|llama|ollama|redis|mosquitto|starnet|docker|voice|whisper|piper|desktop.commander|tamagotchi' || true; printf '\nUNIT FILES:\n'; systemctl list-unit-files --type=service --no-pager --plain 2>/dev/null | grep -Ei 'airi|sentient|cortana|avatar|llama|ollama|redis|mosquitto|starnet|docker|voice|whisper|piper' || true; printf '\nFAILED:\n'; systemctl --failed --no-pager 2>/dev/null || true"
section unit_metadata bash -c 'for n in airi.service orin-voice.service ollama.service mosquitto.service redis-server.service sentient-core.target sentient-web-chat.service sentient-avatar.service sentient-voice.service sentient-conversation.service sentient-wake-word.service; do printf "\n== %s ==\n" "$n"; systemctl show "$n" --no-pager -p Id -p Description -p LoadState -p ActiveState -p SubState -p UnitFileState -p FragmentPath -p DropInPaths -p MainPID -p User -p WorkingDirectory -p PropagatesStopTo -p BoundBy -p ConsistsOf -p TriggeredBy 2>/dev/null || true; done'
section containers bash -c 'if command -v docker >/dev/null 2>&1; then docker ps -a --format "{{.Names}} | {{.Image}} | {{.Status}} | {{.Ports}}" 2>&1 || true; echo; docker compose ls 2>&1 || true; else echo "docker not installed"; fi; if command -v podman >/dev/null 2>&1; then podman ps -a --format "{{.Names}} | {{.Image}} | {{.Status}}" 2>&1 || true; fi'
section local_models bash -c 'if command -v ollama >/dev/null 2>&1; then ollama --version 2>&1; ollama list 2>&1; ollama ps 2>&1; else echo "ollama not on PATH"; fi; if command -v llama-server >/dev/null 2>&1; then command -v llama-server; fi'
section application_locations bash -c 'for p in /opt/sentient-core /opt/sentient /home/cortana/sentient-core /home/cortana/cortana/backend /mnt/orin-ai /opt/airi /home/nano/airi /home/cortana/airi; do if [ -e "$p" ]; then ls -ld "$p"; if [ -d "$p/.git" ]; then (cd "$p" && printf "git_commit: " && git rev-parse HEAD && printf "git_branch: " && git branch --show-current && printf "dirty_files_count: " && git status --porcelain | wc -l) 2>/dev/null || true; fi; fi; done; echo; printf "AIRI-related service paths (names only):\n"; find /opt /etc/systemd/system /home -maxdepth 3 -iname "*airi*" -print 2>/dev/null | head -80'
section audio bash -c 'if command -v arecord >/dev/null 2>&1; then arecord -l 2>&1; fi; if command -v aplay >/dev/null 2>&1; then aplay -l 2>&1; fi; if command -v pactl >/dev/null 2>&1; then pactl list short sources 2>&1; pactl list short sinks 2>&1; fi'
section endpoints bash -c 'for url in http://127.0.0.1:11434/api/tags http://127.0.0.1:18080/v1/models http://127.0.0.1:3001/ http://127.0.0.1:8001/ http://127.0.0.1:8002/ http://127.0.0.1:8003/ http://127.0.0.1:9001/; do printf "%s: " "$url"; if command -v curl >/dev/null 2>&1; then curl -sS --max-time 2 -o /dev/null -w "%{http_code}\n" "$url" 2>/dev/null || echo unreachable; else echo no-curl; fi; done'
section timers bash -c 'systemctl list-timers --all --no-pager --plain 2>/dev/null | grep -Ei "airi|sentient|cortana|avatar|llama|ollama|starnet" || true'
cat >"${out}/README.txt" <<'NOTE'
READ-ONLY AUDIT EVIDENCE — MAZLABZ ORIN NANO
No services were modified by this collector.
Review the contents LOCALLY before sharing: filenames, host/network details and
process/container names may still be operationally sensitive. No secrets,
environment-file contents, docker inspect output, process argument lists or
journal bodies were requested by this script.
A missing output may be due to permissions or absent tooling; it is not proof
that the service does not exist.
Never share SSH credentials, API keys, access tokens or private host keys.
NOTE
printf '\nDone. Evidence folder: %s\n' "$out"


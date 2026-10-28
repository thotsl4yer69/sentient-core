#!/usr/bin/env bash
# Runs on a separate OpenShell gateway host. Nothing is changed on the Nano.
# Listener binds ONLY to Docker's host-gateway interface, never LAN/WAN.
set -euo pipefail
: "${NANO_SSH_HOST:?Set NANO_SSH_HOST to the Nano SSH endpoint}"
: "${NANO_SSH_USER:?Set NANO_SSH_USER to the Nano SSH username}"
[[ "$NANO_SSH_HOST" =~ ^[a-zA-Z0-9][a-zA-Z0-9.:-]*$ ]] || { echo 'Invalid Nano host' >&2; exit 2; }
[[ "$NANO_SSH_USER" =~ ^[a-z_][a-zA-Z0-9_-]*$ ]] || { echo 'Invalid Nano user' >&2; exit 2; }
command -v ssh >/dev/null || { echo 'SSH client unavailable' >&2; exit 2; }
command -v docker >/dev/null || { echo 'Docker is required on OpenShell gateway' >&2; exit 2; }
bind_ip="$(docker network inspect bridge --format '{{(index .IPAM.Config 0).Gateway}}' 2>/dev/null)" || {
  echo 'Docker default bridge not available; cannot determine sandbox host gateway' >&2
  exit 2
}
[[ "$bind_ip" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ && "$bind_ip" != "0.0.0.0" ]] || {
  echo "Unsafe Docker gateway address: $bind_ip" >&2
  exit 2
}
printf 'Forwarding Docker-only %s:11435 -> Nano loopback Ollama 127.0.0.1:11434\n' "$bind_ip" >&2
exec ssh -N -T -o BatchMode=yes -o ExitOnForwardFailure=yes -o StrictHostKeyChecking=yes \
  -o ServerAliveInterval=25 -o ServerAliveCountMax=3 \
  -L "${bind_ip}:11435:127.0.0.1:11434" -- "$NANO_SSH_USER@$NANO_SSH_HOST"

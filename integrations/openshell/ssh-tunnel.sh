#!/usr/bin/env bash
# Run on the separate OpenShell gateway host. Does not modify the Nano.
set -euo pipefail
: "${NANO_SSH_HOST:?Set NANO_SSH_HOST to the address reachable from gateway}"
: "${NANO_SSH_USER:?Set NANO_SSH_USER to the Nano SSH username}"
[[ "$NANO_SSH_HOST" =~ ^[a-zA-Z0-9][a-zA-Z0-9.:-]*$ ]] || { echo 'Invalid Nano host' >&2; exit 2; }
[[ "$NANO_SSH_USER" =~ ^[a-z_][a-zA-Z0-9_-]*$ ]] || { echo 'Invalid Nano user' >&2; exit 2; }
command -v ssh >/dev/null || { echo 'SSH client unavailable' >&2; exit 2; }
# Fail closed if host key is not already trusted or SSH key-based login is unavailable.
exec ssh -N -T -o BatchMode=yes -o ExitOnForwardFailure=yes -o StrictHostKeyChecking=yes \
  -o ServerAliveInterval=25 -o ServerAliveCountMax=3 \
  -L 127.0.0.1:11435:127.0.0.1:11434 -- "$NANO_SSH_USER@$NANO_SSH_HOST"

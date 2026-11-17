#!/usr/bin/env bash
# Write the Mosquitto dynamic-security database, plugin snippet, and local CA.
# Passwords come from SC_MQTT_PASS_GATEWAY, SC_MQTT_PASS_BRAIN, SC_MQTT_PASS_SPEECH,
# SC_MQTT_PASS_BUDDY, SC_MQTT_PASS_MEMORY, SC_MQTT_PASS_COMPAT, and SC_MQTT_PASS_HEALTH.
# Each value is 16 to 128 letters or digits. This script does not print them
# and does not restart the running broker.
set -euo pipefail

ROOT=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
DEST=/etc/mosquitto/sc

usage() {
  echo "usage: init-broker.sh [--dest DIR]" >&2
  echo "Writes the broker auth database and local CA under DIR (default /etc/mosquitto/sc)." >&2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h)
      usage
      exit 0
      ;;
    --dest)
      DEST=${2:?--dest needs a directory}
      shift 2
      ;;
    *)
      usage
      exit 2
      ;;
  esac
done

users=(gateway brain speech buddy memory compat health)
for suffix in "${users[@]}"; do
  var="SC_MQTT_PASS_${suffix^^}"
  value="${!var-}"
  if [[ ${#value} -lt 16 ]]; then
    echo "sc-mosquitto: ${var} must be set to 16 or more letters or digits" >&2
    exit 2
  fi
done

python3 "$ROOT/apply_acl.py" bootstrap --dest "$DEST" --acl "$ROOT/acl"
python3 "$ROOT/issue_certs.py" --dest "$DEST/certs"

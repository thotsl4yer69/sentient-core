#!/bin/bash

# Sentient Core Systemd Service Uninstallation Script
# This script stops and disables all Sentient Core services

set -e

SYSTEMD_DIR="/etc/systemd/system"

# Service list
SERVICES=(
    "sentient-wake-word"
    "sentient-perception"
    "sentient-contemplation"
    "sentient-memory"
    "sentient-proactive"
    "sentient-conversation"
    "sentient-avatar-bridge"
    "sentient-web-chat"
    "sentient-voice"
)

echo "Uninstalling Sentient Core systemd services..."
echo

# Check if running as root
if [[ $EUID -ne 0 ]]; then
   echo "ERROR: This script must be run as root (use sudo)"
   exit 1
fi

# Stop all services
echo "Stopping services..."
for service in "${SERVICES[@]}"; do
    if systemctl is-active --quiet "${service}.service"; then
        systemctl stop "${service}.service"
        echo "  ✓ Stopped ${service}.service"
    else
        echo "  - ${service}.service not running"
    fi
done

echo
echo "Disabling services from auto-start..."
for service in "${SERVICES[@]}"; do
    if systemctl is-enabled --quiet "${service}.service" 2>/dev/null; then
        systemctl disable "${service}.service"
        echo "  ✓ Disabled ${service}.service"
    else
        echo "  - ${service}.service already disabled"
    fi
done

echo
echo "Removing service files from $SYSTEMD_DIR..."
for service in "${SERVICES[@]}"; do
    if [[ -f "$SYSTEMD_DIR/${service}.service" ]]; then
        rm "$SYSTEMD_DIR/${service}.service"
        echo "  ✓ Removed ${service}.service"
    fi
done

echo
echo "Reloading systemd daemon..."
systemctl daemon-reload

echo
echo "Uninstallation complete!"
echo "All Sentient Core services have been stopped and disabled."

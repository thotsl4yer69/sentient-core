#!/bin/bash

# Sentient Core Systemd Service Installation Script
# This script copies all service files to /etc/systemd/system/ and enables them

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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

echo "Installing Sentient Core systemd services..."
echo

# Check if running as root
if [[ $EUID -ne 0 ]]; then
   echo "ERROR: This script must be run as root (use sudo)"
   exit 1
fi

# Copy service files
echo "Copying service files to $SYSTEMD_DIR..."
for service in "${SERVICES[@]}"; do
    if [[ ! -f "$SCRIPT_DIR/${service}.service" ]]; then
        echo "ERROR: Service file not found: $SCRIPT_DIR/${service}.service"
        exit 1
    fi
    
    cp "$SCRIPT_DIR/${service}.service" "$SYSTEMD_DIR/${service}.service"
    echo "  ✓ Copied ${service}.service"
done

echo
echo "Reloading systemd daemon..."
systemctl daemon-reload

echo "Enabling services to auto-start..."
for service in "${SERVICES[@]}"; do
    systemctl enable "${service}.service"
    echo "  ✓ Enabled ${service}.service"
done

echo
echo "Installation complete!"
echo
echo "To start all services immediately, run:"
echo "  sudo systemctl start sentient-wake-word.service"
echo "  sudo systemctl start sentient-perception.service"
echo "  sudo systemctl start sentient-contemplation.service"
echo "  sudo systemctl start sentient-memory.service"
echo "  sudo systemctl start sentient-proactive.service"
echo "  sudo systemctl start sentient-conversation.service"
echo "  sudo systemctl start sentient-avatar-bridge.service"
echo "  sudo systemctl start sentient-web-chat.service"
echo "  sudo systemctl start sentient-voice.service"
echo
echo "Or start all at once:"
echo "  sudo systemctl start sentient-*.service"
echo
echo "To view service status:"
echo "  sudo systemctl status sentient-*.service"
echo
echo "To view logs:"
echo "  sudo journalctl -u sentient-wake-word.service -f"
echo "  (replace service name to view different service logs)"

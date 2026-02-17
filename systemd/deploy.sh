#!/bin/bash
# Deploy v2 systemd service files
# Usage: sudo ./deploy.sh

set -e

SYSTEMD_DIR="/etc/systemd/system"
SOURCE_DIR="$(dirname "$0")"

echo "Deploying Sentient Core v2 systemd services..."

# Stop existing target
echo "Stopping existing services..."
systemctl stop sentient-core.target 2>/dev/null || true

# Copy all service files
echo "Copying service files..."
for f in "$SOURCE_DIR"/*.service "$SOURCE_DIR"/*.target; do
    [ -f "$f" ] && cp "$f" "$SYSTEMD_DIR/"
done

# Reload systemd
echo "Reloading systemd daemon..."
systemctl daemon-reload

# Enable target and all services
echo "Enabling services..."
systemctl enable sentient-core.target
for f in "$SOURCE_DIR"/*.service; do
    name=$(basename "$f")
    systemctl enable "$name"
done

echo "Deployment complete!"
echo "Start with: sudo systemctl start sentient-core.target"

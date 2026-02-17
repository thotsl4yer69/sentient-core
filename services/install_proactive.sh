#!/bin/bash
# Installation script for Proactive Behavior Engine

set -e

echo "=========================================="
echo "Proactive Behavior Engine Installation"
echo "=========================================="
echo ""

# Check if running as root for systemd installation
if [ "$EUID" -ne 0 ] && [ "$1" != "--user-only" ]; then
    echo "Please run as root for full installation, or use --user-only flag"
    echo "  sudo $0"
    echo "  OR"
    echo "  $0 --user-only"
    exit 1
fi

# Install Python dependencies
echo "[1/4] Installing Python dependencies..."
pip3 install -r /opt/sentient-core/services/requirements-proactive.txt
echo "✓ Dependencies installed"
echo ""

# Verify Redis is running
echo "[2/4] Checking Redis..."
if systemctl is-active --quiet redis-server; then
    echo "✓ Redis is running"
else
    echo "⚠ Redis is not running"
    echo "  Starting Redis..."
    systemctl start redis-server
    echo "✓ Redis started"
fi
echo ""

# Verify MQTT is running
echo "[3/4] Checking MQTT broker..."
if systemctl is-active --quiet mosquitto; then
    echo "✓ MQTT broker is running"
else
    echo "⚠ MQTT broker is not running"
    echo "  Starting MQTT broker..."
    systemctl start mosquitto
    echo "✓ MQTT broker started"
fi
echo ""

# Install systemd service (if root)
if [ "$1" != "--user-only" ]; then
    echo "[4/4] Installing systemd service..."
    cp /opt/sentient-core/systemd/sentient-proactive.service /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable sentient-proactive.service
    echo "✓ Systemd service installed and enabled"
    echo ""

    # Start service
    echo "Starting proactive behavior engine..."
    systemctl start sentient-proactive.service
    sleep 2

    # Check status
    if systemctl is-active --quiet sentient-proactive.service; then
        echo "✓ Service is running"
    else
        echo "✗ Service failed to start"
        echo ""
        echo "Check logs with:"
        echo "  sudo journalctl -u sentient-proactive.service -n 50"
        exit 1
    fi
else
    echo "[4/4] Skipping systemd installation (--user-only mode)"
fi

echo ""
echo "=========================================="
echo "Installation Complete!"
echo "=========================================="
echo ""
echo "Service Status:"
systemctl status sentient-proactive.service --no-pager || true
echo ""
echo "Useful Commands:"
echo "  Check status:   sudo systemctl status sentient-proactive.service"
echo "  View logs:      sudo journalctl -u sentient-proactive.service -f"
echo "  Restart:        sudo systemctl restart sentient-proactive.service"
echo "  Stop:           sudo systemctl stop sentient-proactive.service"
echo ""
echo "Testing:"
echo "  Test trigger:   python3 /opt/sentient-core/services/test_proactive.py boredom"
echo "  Listen output:  python3 /opt/sentient-core/services/test_proactive.py listen"
echo "  Check cooldown: python3 /opt/sentient-core/services/test_proactive.py cooldowns"
echo ""
echo "Documentation: /opt/sentient-core/services/PROACTIVE_ENGINE.md"
echo ""

#!/bin/bash
# Memory System Installation Script

set -e

echo "============================================"
echo "Sentient Core - Memory System Installation"
echo "============================================"
echo

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    echo "Error: Do not run as root. This script will use sudo when needed."
    exit 1
fi

# 1. Install system dependencies
echo "1. Installing system dependencies..."
sudo apt update
sudo apt install -y redis-server mosquitto mosquitto-clients python3-pip

# 2. Enable and start services
echo
echo "2. Starting Redis and Mosquitto..."
sudo systemctl enable redis-server
sudo systemctl start redis-server
sudo systemctl enable mosquitto
sudo systemctl start mosquitto

# 3. Install Python dependencies
echo
echo "3. Installing Python dependencies..."
cd /opt/sentient-core
pip3 install -r requirements.txt

# 4. Test Redis connection
echo
echo "4. Testing Redis connection..."
if redis-cli ping | grep -q PONG; then
    echo "✓ Redis is running"
else
    echo "✗ Redis connection failed"
    exit 1
fi

# 5. Test MQTT connection
echo
echo "5. Testing MQTT connection..."
if timeout 2 mosquitto_sub -t "test" -C 1 > /dev/null 2>&1 & then
    echo "✓ MQTT is running"
else
    echo "✓ MQTT installed (broker may need configuration)"
fi

# 6. Make scripts executable
echo
echo "6. Setting permissions..."
chmod +x /opt/sentient-core/services/memory.py
chmod +x /opt/sentient-core/services/memory_api.py
chmod +x /opt/sentient-core/services/memory_cli.py
chmod +x /opt/sentient-core/services/memory_integration_example.py
chmod +x /opt/sentient-core/services/test_memory.py

# 7. Run basic test
echo
echo "7. Running basic functionality test..."
cd /opt/sentient-core/services
if python3 -c "from memory import MemorySystem; print('✓ Import successful')"; then
    echo "✓ Memory module loads correctly"
else
    echo "✗ Memory module failed to load"
    exit 1
fi

# 8. Install systemd service (optional)
echo
read -p "Install systemd service? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    if [ -f /opt/sentient-core/systemd/sentient-memory.service ]; then
        sudo cp /opt/sentient-core/systemd/sentient-memory.service /etc/systemd/system/
        sudo systemctl daemon-reload
        echo "✓ Systemd service installed"
        echo
        read -p "Enable and start service now? (y/n) " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            sudo systemctl enable sentient-memory
            sudo systemctl start sentient-memory
            echo "✓ Service started"
            echo
            echo "Check status with: sudo systemctl status sentient-memory"
        fi
    else
        echo "✗ Service file not found"
    fi
fi

# 9. Display summary
echo
echo "============================================"
echo "Installation Complete!"
echo "============================================"
echo
echo "Quick Start:"
echo "  cd /opt/sentient-core/services"
echo
echo "  # Run demo"
echo "  python3 memory.py"
echo
echo "  # Run integration examples"
echo "  python3 memory_integration_example.py"
echo
echo "  # Use CLI"
echo "  python3 memory_cli.py stats"
echo "  python3 memory_cli.py store \"Hello\" \"Hi there!\""
echo "  python3 memory_cli.py search \"greeting\""
echo
echo "  # Run tests"
echo "  python3 test_memory.py"
echo
echo "Documentation: /opt/sentient-core/services/MEMORY_README.md"
echo
echo "Service logs (if installed):"
echo "  sudo journalctl -u sentient-memory -f"
echo

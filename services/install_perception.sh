#!/bin/bash
# Perception Layer Installation Script

set -e

echo "=========================================="
echo "Sentient Core Perception Layer Installer"
echo "=========================================="

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if running as root for service installation
if [ "$EUID" -ne 0 ] && [ "$1" == "--install-service" ]; then
    echo -e "${RED}Error: Service installation requires root privileges${NC}"
    echo "Run with: sudo $0 --install-service"
    exit 1
fi

# Function to print status
print_status() {
    echo -e "${GREEN}✓${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

# Check Python version
echo ""
echo "Checking Python version..."
PYTHON_VERSION=$(python3 --version | awk '{print $2}')
PYTHON_MAJOR=$(echo $PYTHON_VERSION | cut -d. -f1)
PYTHON_MINOR=$(echo $PYTHON_VERSION | cut -d. -f2)

if [ "$PYTHON_MAJOR" -ge 3 ] && [ "$PYTHON_MINOR" -ge 7 ]; then
    print_status "Python $PYTHON_VERSION detected"
else
    print_error "Python 3.7+ required (found $PYTHON_VERSION)"
    exit 1
fi

# Install system dependencies
echo ""
echo "Installing system dependencies..."

if command -v apt-get &> /dev/null; then
    print_status "Using apt package manager"

    # Check if PortAudio is installed
    if ! dpkg -l | grep -q portaudio19-dev; then
        echo "Installing PortAudio..."
        sudo apt-get update
        sudo apt-get install -y portaudio19-dev
        print_status "PortAudio installed"
    else
        print_status "PortAudio already installed"
    fi
else
    print_warning "apt-get not found, skipping system dependencies"
    echo "Please manually install: portaudio19-dev"
fi

# Install Python dependencies
echo ""
echo "Installing Python dependencies..."

pip3 install --upgrade pip

# Install required packages
PACKAGES=("aiomqtt" "paho-mqtt" "pyaudio")

for package in "${PACKAGES[@]}"; do
    echo "Installing $package..."
    pip3 install --upgrade "$package"
    if pip3 show "$package" &> /dev/null; then
        print_status "$package installed"
    else
        print_error "Failed to install $package"
        exit 1
    fi
done

# Verify MQTT broker
echo ""
echo "Checking MQTT broker..."

if systemctl is-active --quiet mosquitto; then
    print_status "Mosquitto broker is running"
elif command -v mosquitto &> /dev/null; then
    print_warning "Mosquitto installed but not running"
    echo "Start with: sudo systemctl start mosquitto"
else
    print_warning "Mosquitto broker not found"
    echo "Install with: sudo apt-get install mosquitto mosquitto-clients"
fi

# Test perception service
echo ""
echo "Testing perception service..."

if python3 -c "import perception; print('Import successful')" 2>&1 | grep -q "Import successful"; then
    print_status "Perception module imports successfully"
else
    print_error "Perception module import failed"
    python3 -c "import perception"
    exit 1
fi

# Install systemd service
if [ "$1" == "--install-service" ]; then
    echo ""
    echo "Installing systemd service..."

    # Copy service file
    cp /opt/sentient-core/services/perception.service /etc/systemd/system/
    print_status "Service file copied"

    # Reload systemd
    systemctl daemon-reload
    print_status "Systemd reloaded"

    # Enable service
    systemctl enable perception
    print_status "Service enabled"

    echo ""
    echo "Service installation complete!"
    echo ""
    echo "Control commands:"
    echo "  Start:   sudo systemctl start perception"
    echo "  Stop:    sudo systemctl stop perception"
    echo "  Status:  sudo systemctl status perception"
    echo "  Logs:    sudo journalctl -u perception -f"
fi

# Summary
echo ""
echo "=========================================="
echo "Installation Summary"
echo "=========================================="
print_status "Python dependencies installed"
print_status "Perception service ready"

if [ "$1" != "--install-service" ]; then
    echo ""
    echo "To install as system service, run:"
    echo "  sudo $0 --install-service"
fi

echo ""
echo "Test the service:"
echo "  python3 /opt/sentient-core/services/test_perception.py"
echo ""
echo "Run manually:"
echo "  python3 /opt/sentient-core/services/perception.py"
echo ""

print_status "Installation complete!"

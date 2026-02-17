#!/bin/bash
# Installation script for Voice-First Mode
# Part of Sentient Core

set -e  # Exit on error

echo "========================================="
echo "Sentient Core - Voice-First Mode Setup"
echo "========================================="
echo ""

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    echo "Error: Do not run this script as root"
    echo "It will use sudo when needed"
    exit 1
fi

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Step 1: Install system dependencies
echo -e "${YELLOW}Step 1: Installing system dependencies...${NC}"
sudo apt-get update
sudo apt-get install -y \
    portaudio19-dev \
    python3-pyaudio \
    libportaudio2 \
    mosquitto \
    mosquitto-clients \
    alsa-utils

echo -e "${GREEN}✓ System dependencies installed${NC}"
echo ""

# Step 2: Install Python dependencies
echo -e "${YELLOW}Step 2: Installing Python dependencies...${NC}"

if [ -f "/opt/sentient-core/requirements-voice.txt" ]; then
    pip install -r /opt/sentient-core/requirements-voice.txt
else
    # Install manually if requirements file missing
    pip install aiomqtt paho-mqtt PyAudio webrtcvad faster-whisper numpy
fi

echo -e "${GREEN}✓ Python dependencies installed${NC}"
echo ""

# Step 3: Create log directory
echo -e "${YELLOW}Step 3: Creating log directory...${NC}"
sudo mkdir -p /var/log/sentient
sudo chown $USER:$USER /var/log/sentient
echo -e "${GREEN}✓ Log directory created${NC}"
echo ""

# Step 4: Test installation
echo -e "${YELLOW}Step 4: Testing installation...${NC}"
python3 /opt/sentient-core/interfaces/test_voice.py

if [ $? -eq 0 ]; then
    echo ""
    echo -e "${GREEN}✓ All tests passed!${NC}"
else
    echo ""
    echo -e "${RED}✗ Some tests failed. Please check the output above.${NC}"
    exit 1
fi

echo ""

# Step 5: Install systemd service
echo -e "${YELLOW}Step 5: Installing systemd service...${NC}"
read -p "Install systemd service? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    sudo cp /opt/sentient-core/systemd/sentient-voice.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable sentient-voice
    echo -e "${GREEN}✓ Systemd service installed and enabled${NC}"
    echo ""
    echo "Start the service with:"
    echo "  sudo systemctl start sentient-voice"
    echo ""
    echo "View logs with:"
    echo "  sudo journalctl -u sentient-voice -f"
else
    echo "Skipped systemd installation"
fi

echo ""
echo "========================================="
echo -e "${GREEN}Installation Complete!${NC}"
echo "========================================="
echo ""
echo "Next steps:"
echo "  1. Start required services:"
echo "     sudo systemctl start mosquitto"
echo "     sudo systemctl start sentient-wake-word"
echo "     sudo systemctl start sentient-whisper-stt"
echo "     sudo systemctl start sentient-piper-tts"
echo ""
echo "  2. Start voice-first mode:"
echo "     sudo systemctl start sentient-voice"
echo ""
echo "  3. Monitor logs:"
echo "     sudo journalctl -u sentient-voice -f"
echo ""
echo "Documentation: /opt/sentient-core/interfaces/VOICE_MODE.md"
echo ""

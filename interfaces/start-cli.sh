#!/bin/bash
# Quick-start script for Sentient Core CLI
# Usage: ./start-cli.sh [--debug] [--host HOST] [--port PORT]

set -e

# Get script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}╔════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║  Sentient Core Terminal CLI Launcher   ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════╝${NC}"
echo ""

# Check Python
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}ERROR: Python 3 is required but not installed${NC}"
    exit 1
fi

python_version=$(python3 --version | cut -d' ' -f2)
echo -e "${GREEN}✓ Python ${python_version} found${NC}"

# Check required packages
echo -e "${YELLOW}Checking dependencies...${NC}"

missing_packages=()

if ! python3 -c "import aiomqtt" 2>/dev/null; then
    missing_packages+=("aiomqtt")
fi

if ! python3 -c "import colorama" 2>/dev/null; then
    missing_packages+=("colorama")
fi

if [ ${#missing_packages[@]} -gt 0 ]; then
    echo -e "${YELLOW}Missing packages: ${missing_packages[*]}${NC}"
    echo -e "${YELLOW}Installing dependencies...${NC}"
    pip install aiomqtt colorama
fi

echo -e "${GREEN}✓ All dependencies installed${NC}"
echo ""

# Create log directory
log_dir="/var/log/sentient"
if [ ! -d "$log_dir" ]; then
    echo -e "${YELLOW}Creating log directory: $log_dir${NC}"
    mkdir -p "$log_dir" || {
        echo -e "${YELLOW}Note: Could not create $log_dir, using /tmp instead${NC}"
        # Set alternate log dir if needed
        export SENTIENT_LOG_DIR="/tmp/sentient-logs"
        mkdir -p "$SENTIENT_LOG_DIR"
    }
fi

echo -e "${GREEN}✓ Log directory ready${NC}"
echo ""

# Check if conversation service is running
mqtt_port=${2:-1883}
if ! nc -z localhost "$mqtt_port" 2>/dev/null && ! lsof -i ":$mqtt_port" &>/dev/null; then
    echo -e "${YELLOW}⚠ Warning: MQTT broker may not be running on port $mqtt_port${NC}"
    echo -e "${YELLOW}Start it with: mosquitto${NC}"
    echo ""
fi

echo -e "${GREEN}✓ Environment ready${NC}"
echo ""

# Run CLI
echo -e "${BLUE}Starting Cortana Terminal Interface...${NC}"
echo ""

cd "$PROJECT_ROOT"
python3 interfaces/cli.py "$@"

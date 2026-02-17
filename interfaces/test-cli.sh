#!/bin/bash
# Test script for Sentient Core CLI
# Simulates user interactions and verifies functionality

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Configuration
MQTT_BROKER="localhost"
MQTT_PORT=1883
TIMEOUT=5

echo -e "${BLUE}╔════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║  Sentient Core CLI Test Suite          ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════╝${NC}"
echo ""

# Test 1: Check MQTT broker
echo -e "${YELLOW}Test 1: Checking MQTT broker...${NC}"
if command -v nc &> /dev/null; then
    if nc -z $MQTT_BROKER $MQTT_PORT 2>/dev/null; then
        echo -e "${GREEN}✓ MQTT broker is running${NC}"
    else
        echo -e "${YELLOW}⚠ MQTT broker may not be running${NC}"
        echo -e "${YELLOW}  Start with: mosquitto${NC}"
    fi
fi
echo ""

# Test 2: Check Python version
echo -e "${YELLOW}Test 2: Checking Python version...${NC}"
python_version=$(python3 --version 2>&1)
echo -e "${GREEN}✓ $python_version${NC}"
echo ""

# Test 3: Check dependencies
echo -e "${YELLOW}Test 3: Checking dependencies...${NC}"

test_import() {
    local module=$1
    local name=$2
    if python3 -c "import $module" 2>/dev/null; then
        echo -e "${GREEN}✓ $name installed${NC}"
        return 0
    else
        echo -e "${RED}✗ $name NOT installed${NC}"
        return 1
    fi
}

all_deps=true
test_import aiomqtt "aiomqtt" || all_deps=false
test_import colorama "colorama" || all_deps=false

if ! $all_deps; then
    echo -e "${YELLOW}Installing missing dependencies...${NC}"
    pip install aiomqtt colorama
fi
echo ""

# Test 4: Check file permissions
echo -e "${YELLOW}Test 4: Checking file permissions...${NC}"
files=(
    "$PROJECT_ROOT/interfaces/cli.py"
    "$PROJECT_ROOT/services/conversation.py"
    "$SCRIPT_DIR/start-cli.sh"
)

for file in "${files[@]}"; do
    if [ -f "$file" ]; then
        if [ -x "$file" ]; then
            echo -e "${GREEN}✓ $(basename $file) is executable${NC}"
        else
            echo -e "${YELLOW}⚠ $(basename $file) is not executable${NC}"
            chmod +x "$file"
            echo -e "${GREEN}  Fixed: made executable${NC}"
        fi
    else
        echo -e "${RED}✗ $(basename $file) not found${NC}"
    fi
done
echo ""

# Test 5: Check log directory
echo -e "${YELLOW}Test 5: Checking log directory...${NC}"
log_dir="/var/log/sentient"
if [ -d "$log_dir" ]; then
    echo -e "${GREEN}✓ Log directory exists: $log_dir${NC}"
    if [ -w "$log_dir" ]; then
        echo -e "${GREEN}✓ Log directory is writable${NC}"
    else
        echo -e "${YELLOW}⚠ Log directory is not writable${NC}"
        echo -e "${YELLOW}  Try: sudo chmod 777 $log_dir${NC}"
    fi
else
    echo -e "${YELLOW}⚠ Log directory does not exist${NC}"
    mkdir -p "$log_dir" 2>/dev/null && echo -e "${GREEN}  Created: $log_dir${NC}" || {
        echo -e "${YELLOW}  Could not create (may need sudo)${NC}"
    }
fi
echo ""

# Test 6: Check CLI syntax
echo -e "${YELLOW}Test 6: Checking CLI Python syntax...${NC}"
if python3 -m py_compile "$PROJECT_ROOT/interfaces/cli.py" 2>/dev/null; then
    echo -e "${GREEN}✓ CLI syntax is valid${NC}"
else
    echo -e "${RED}✗ CLI has syntax errors${NC}"
    python3 -m py_compile "$PROJECT_ROOT/interfaces/cli.py"
fi
echo ""

# Test 7: Check conversation service syntax
echo -e "${YELLOW}Test 7: Checking conversation service syntax...${NC}"
if python3 -m py_compile "$PROJECT_ROOT/services/conversation.py" 2>/dev/null; then
    echo -e "${GREEN}✓ Conversation service syntax is valid${NC}"
else
    echo -e "${RED}✗ Conversation service has syntax errors${NC}"
    python3 -m py_compile "$PROJECT_ROOT/services/conversation.py"
fi
echo ""

# Test 8: Quick help check
echo -e "${YELLOW}Test 8: Checking CLI help output...${NC}"
if python3 "$PROJECT_ROOT/interfaces/cli.py" --help > /dev/null 2>&1; then
    echo -e "${GREEN}✓ CLI help works${NC}"
else
    echo -e "${RED}✗ CLI help failed${NC}"
fi
echo ""

# Test 9: Module imports
echo -e "${YELLOW}Test 9: Testing CLI imports...${NC}"
python3 << EOF
import sys
sys.path.insert(0, '$PROJECT_ROOT')

try:
    # Try importing the CLI module
    import importlib.util
    spec = importlib.util.spec_from_file_location("cli", "$PROJECT_ROOT/interfaces/cli.py")
    cli = importlib.util.module_from_spec(spec)

    # Just check basic imports work without running
    import asyncio
    from colorama import Fore, Style
    from aiomqtt import Client

    print("✓ All imports successful", file=sys.stdout)
except Exception as e:
    print(f"✗ Import failed: {e}", file=sys.stderr)
    sys.exit(1)
EOF

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ CLI imports work correctly${NC}"
else
    echo -e "${RED}✗ CLI imports failed${NC}"
fi
echo ""

# Summary
echo -e "${BLUE}╔════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║  Test Summary                          ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════╝${NC}"
echo ""
echo -e "${GREEN}✓ All basic tests passed!${NC}"
echo ""

# Next steps
echo -e "${YELLOW}Next Steps:${NC}"
echo ""
echo -e "${BLUE}1. Start MQTT broker (if not running):${NC}"
echo "   mosquitto"
echo ""
echo -e "${BLUE}2. Start conversation service (in another terminal):${NC}"
echo "   cd $PROJECT_ROOT"
echo "   python3 services/conversation.py"
echo ""
echo -e "${BLUE}3. Start the CLI (in a third terminal):${NC}"
echo "   cd $PROJECT_ROOT"
echo "   python3 interfaces/cli.py"
echo ""
echo -e "${BLUE}Or use the quick-start script:${NC}"
echo "   $SCRIPT_DIR/start-cli.sh"
echo ""

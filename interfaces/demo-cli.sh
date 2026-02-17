#!/bin/bash
# Interactive demo of Sentient Core CLI
# This script starts all services and shows what's running

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

BLUE='\033[0;34m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${BLUE}"
echo "╔═══════════════════════════════════════════════════════════╗"
echo "║     Sentient Core CLI - Interactive Demo                  ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo -e "${NC}"

echo ""
echo -e "${YELLOW}This demo will:${NC}"
echo "1. Check if MQTT broker is running"
echo "2. Start the conversation service in the background"
echo "3. Start the CLI for interactive testing"
echo ""

# Check MQTT
echo -e "${YELLOW}Checking MQTT broker...${NC}"
if ! nc -z localhost 1883 2>/dev/null; then
    echo -e "${YELLOW}⚠ MQTT broker is not running${NC}"
    echo ""
    echo -e "${BLUE}To start MQTT broker, run in another terminal:${NC}"
    echo "  mosquitto"
    echo ""
    echo -e "${YELLOW}Waiting for MQTT broker... (Ctrl+C to skip)${NC}"
    for i in {1..10}; do
        if nc -z localhost 1883 2>/dev/null; then
            echo -e "${GREEN}✓ MQTT broker detected${NC}"
            break
        fi
        echo -n "."
        sleep 1
    done
else
    echo -e "${GREEN}✓ MQTT broker is running${NC}"
fi

echo ""

# Check if conversation service is running
if pgrep -f "python3.*conversation.py" > /dev/null 2>&1; then
    echo -e "${GREEN}✓ Conversation service already running${NC}"
else
    echo -e "${YELLOW}Starting conversation service...${NC}"
    cd "$PROJECT_ROOT"
    python3 services/conversation.py > /tmp/cortana-conversation.log 2>&1 &
    CONV_PID=$!
    sleep 2

    if kill -0 $CONV_PID 2>/dev/null; then
        echo -e "${GREEN}✓ Conversation service started (PID: $CONV_PID)${NC}"
        echo "  Log: /tmp/cortana-conversation.log"
        echo "  Stop with: kill $CONV_PID"
    else
        echo -e "${RED}✗ Failed to start conversation service${NC}"
        exit 1
    fi
fi

echo ""
echo -e "${GREEN}✓ All services ready!${NC}"
echo ""

# Show what to expect
echo -e "${BLUE}What to try in the CLI:${NC}"
echo "  • hello"
echo "  • how are you"
echo "  • what's your name"
echo "  • help"
echo "  • thank you"
echo "  • quit (or Ctrl+C)"
echo ""

# Start CLI
echo -e "${YELLOW}Starting CLI...${NC}"
echo ""

cd "$PROJECT_ROOT"
python3 interfaces/cli.py

# Cleanup
echo ""
echo -e "${YELLOW}Stopping services...${NC}"

# Kill conversation service if we started it
if [ ! -z "$CONV_PID" ] && kill -0 $CONV_PID 2>/dev/null; then
    kill $CONV_PID 2>/dev/null
    echo -e "${GREEN}✓ Stopped conversation service${NC}"
fi

echo -e "${GREEN}Demo complete!${NC}"

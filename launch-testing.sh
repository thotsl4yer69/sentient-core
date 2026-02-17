#!/bin/bash
# Sentient Core v7.0 - Testing Launch Script

echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║     SENTIENT CORE v7.0 - TESTING ENVIRONMENT LAUNCHER         ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo ""

# Verify all services are running
echo "Checking service status..."
all_active=true
for service in wake-word perception contemplation memory proactive conversation avatar-bridge voice web-chat memory-http contemplation-http perception-http; do
    if systemctl is-active --quiet sentient-$service.service; then
        echo "  ✅ sentient-$service"
    else
        echo "  ❌ sentient-$service (INACTIVE)"
        all_active=false
    fi
done

echo ""

if [ "$all_active" = false ]; then
    echo "⚠️  Some services are not running. Start them? (y/n)"
    read -r response
    if [ "$response" = "y" ]; then
        echo "Starting all services..."
        sudo systemctl start sentient-*.service
        sleep 5
        echo "Services started."
    fi
fi

echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║                   SYSTEM READY FOR TESTING                    ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo ""
echo "📋 Testing Options:"
echo ""
echo "1. WEB CHAT (Recommended)"
echo "   URL: http://192.168.1.159:3001"
echo "   - Open in browser"
echo "   - Type messages and chat with Cortana"
echo ""
echo "2. TERMINAL CLI"
echo "   Command: cd /opt/sentient-core/interfaces && python3 cli.py"
echo ""
echo "3. MQTT MONITORING"
echo "   Command: mosquitto_sub -h localhost -u sentient -P sentient1312 -t 'sentient/#' -v"
echo ""
echo "4. SERVICE LOGS"
echo "   Command: sudo journalctl -u sentient-conversation.service -f"
echo ""
echo "5. HTTP API TESTS"
echo "   Memory:       curl http://localhost:8001/health | jq"
echo "   Contemplation: curl http://localhost:8002/health | jq"
echo "   Perception:    curl http://localhost:8003/health | jq"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "What would you like to do?"
echo ""
echo "  [1] Open Web Chat URL instructions"
echo "  [2] Launch Terminal CLI"
echo "  [3] Monitor MQTT messages"
echo "  [4] Watch conversation logs"
echo "  [5] Test HTTP APIs"
echo "  [6] View full testing guide"
echo "  [Q] Quit"
echo ""
read -p "Select option (1-6 or Q): " choice

case $choice in
    1)
        echo ""
        echo "🌐 WEB CHAT INTERFACE"
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo ""
        echo "Open this URL in your browser:"
        echo "  http://192.168.1.159:3001"
        echo ""
        echo "Features:"
        echo "  - Brutalist cyber-neon dark theme"
        echo "  - Real-time WebSocket messaging"
        echo "  - Emotion state indicator"
        echo "  - Thinking animation"
        echo "  - Voice input button (if microphone available)"
        echo ""
        echo "Try sending: 'Hello Cortana, how are you?'"
        echo ""
        ;;
    2)
        echo ""
        echo "🖥️  LAUNCHING TERMINAL CLI..."
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo ""
        cd /opt/sentient-core/interfaces
        python3 cli.py
        ;;
    3)
        echo ""
        echo "📡 MQTT MESSAGE MONITORING"
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo ""
        echo "Subscribing to all Sentient Core MQTT topics..."
        echo "Press Ctrl+C to stop"
        echo ""
        sleep 2
        mosquitto_sub -h localhost -u sentient -P sentient1312 -t "sentient/#" -v
        ;;
    4)
        echo ""
        echo "📋 CONVERSATION SERVICE LOGS"
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo ""
        echo "Live log streaming... Press Ctrl+C to stop"
        echo ""
        sleep 2
        sudo journalctl -u sentient-conversation.service -f
        ;;
    5)
        echo ""
        echo "🔌 HTTP API HEALTH CHECKS"
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo ""
        echo "Memory API (port 8001):"
        curl -s http://localhost:8001/health | jq || echo "  ❌ Not available"
        echo ""
        echo "Contemplation API (port 8002):"
        curl -s http://localhost:8002/health | jq || echo "  ❌ Not available"
        echo ""
        echo "Perception API (port 8003):"
        curl -s http://localhost:8003/health | jq || echo "  ❌ Not available"
        echo ""
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo ""
        ;;
    6)
        echo ""
        echo "📖 Opening testing guide..."
        less /opt/sentient-core/TESTING_GUIDE.md
        ;;
    [Qq])
        echo ""
        echo "Testing launcher closed."
        exit 0
        ;;
    *)
        echo ""
        echo "Invalid option. Please run the script again."
        ;;
esac

echo ""
echo "Testing session complete."
echo ""

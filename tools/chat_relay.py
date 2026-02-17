#!/usr/bin/env python3
"""
Sentient Core - Chat Relay
Send a single message to Cortana via MQTT and capture the full response.
Usage: python3 chat_relay.py "your message here"
"""
import paho.mqtt.client as mqtt
import json
import sys
import threading
import time

class ChatRelay:
    def __init__(self):
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.response_tokens = []
        self.done_event = threading.Event()
        self.connected_event = threading.Event()
        self.state = ""

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        client.subscribe("sentient/chat/output")
        client.subscribe("sentient/chat/stream")
        client.subscribe("sentient/conversation/state")
        self.connected_event.set()

    def _on_message(self, client, userdata, msg, properties=None):
        try:
            payload = json.loads(msg.payload.decode())
        except json.JSONDecodeError:
            return

        if msg.topic == "sentient/chat/stream":
            token = payload.get("token", "")
            if token:
                self.response_tokens.append(token)
            if payload.get("done"):
                pass  # wait for chat/output for final signal

        elif msg.topic == "sentient/chat/output":
            self.done_event.set()

        elif msg.topic == "sentient/conversation/state":
            self.state = payload.get("state", "")

    def send(self, text, timeout=90):
        try:
            self.client.connect("localhost", 1883)
        except Exception as e:
            return f"[MQTT connection failed: {e}]"

        self.client.loop_start()

        if not self.connected_event.wait(timeout=5):
            self.client.loop_stop()
            return "[MQTT connect timeout]"

        msg = json.dumps({
            "text": text,
            "user": "User",
            "source": "relay",
        })
        self.client.publish("sentient/chat/input", msg, qos=1)

        if not self.done_event.wait(timeout=timeout):
            self.client.loop_stop()
            self.client.disconnect()
            partial = "".join(self.response_tokens)
            if partial:
                return partial + "\n[response timed out - partial]"
            return "[no response within timeout]"

        self.client.loop_stop()
        self.client.disconnect()
        return "".join(self.response_tokens)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 chat_relay.py \"your message\"")
        sys.exit(1)

    message = " ".join(sys.argv[1:])
    relay = ChatRelay()
    response = relay.send(message)
    print(response)

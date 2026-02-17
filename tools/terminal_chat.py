#!/usr/bin/env python3
"""
Sentient Core - Terminal Chat
Interactive terminal interface for chatting with Cortana via MQTT.
"""
import paho.mqtt.client as mqtt
import json
import sys
import threading
import readline  # enables arrow keys and history in input()

CYAN = "\033[36m"
GREEN = "\033[32m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"
CLEAR_LINE = "\r\033[K"

class TerminalChat:
    def __init__(self):
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.streaming = False
        self.done_event = threading.Event()

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        client.subscribe("sentient/chat/output")
        client.subscribe("sentient/chat/stream")
        client.subscribe("sentient/conversation/state")

    def _on_message(self, client, userdata, msg, properties=None):
        topic = msg.topic
        try:
            payload = json.loads(msg.payload.decode())
        except json.JSONDecodeError:
            return

        if topic == "sentient/chat/stream":
            token = payload.get("token", "")
            if token:
                if not self.streaming:
                    self.streaming = True
                    print(f"\n{CYAN}{BOLD}CORTANA:{RESET} {CYAN}", end="", flush=True)
                print(token, end="", flush=True)
            if payload.get("done"):
                print(RESET)
                self.streaming = False

        elif topic == "sentient/chat/output":
            self.done_event.set()

        elif topic == "sentient/conversation/state":
            state = payload.get("state", "")
            if state == "processing":
                print(f"{DIM}  [thinking...]{RESET}", end="", flush=True)

    def send(self, text):
        self.done_event.clear()
        self.streaming = False
        msg = json.dumps({
            "text": text,
            "user": "User",
            "source": "terminal",
        })
        self.client.publish("sentient/chat/input", msg, qos=1)

    def run(self):
        try:
            self.client.connect("localhost", 1883)
        except Exception as e:
            print(f"Failed to connect to MQTT: {e}")
            sys.exit(1)

        self.client.loop_start()

        print(f"{BOLD}{'='*50}{RESET}")
        print(f"{CYAN}{BOLD}  CORTANA // SENTIENT CORE{RESET}")
        print(f"{DIM}  Terminal Chat Interface{RESET}")
        print(f"{DIM}  Type 'quit' or Ctrl+C to exit{RESET}")
        print(f"{BOLD}{'='*50}{RESET}")
        print()

        try:
            while True:
                try:
                    user_input = input(f"{GREEN}{BOLD}YOU:{RESET} ")
                except EOFError:
                    break

                if not user_input.strip():
                    continue
                if user_input.strip().lower() in ("quit", "exit", "q"):
                    break

                self.send(user_input.strip())

                # Wait for response to complete
                if not self.done_event.wait(timeout=60):
                    print(f"\n{DIM}[timeout - no response]{RESET}")

        except KeyboardInterrupt:
            print(f"\n{DIM}[disconnected]{RESET}")

        self.client.loop_stop()
        self.client.disconnect()
        print(f"\n{CYAN}Goodbye!{RESET}")


if __name__ == "__main__":
    TerminalChat().run()

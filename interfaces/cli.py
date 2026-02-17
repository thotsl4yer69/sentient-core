#!/usr/bin/env python3
"""
Sentient Core Terminal CLI Interface
Production-ready MQTT-based conversational interface for testing and development.

Features:
- Interactive stdin/stdout interface
- MQTT-based communication with conversation service
- Colored output using colorama
- Real-time emotion state display
- Thinking indicator with spinner
- Debug mode for internal deliberation visibility
- Graceful shutdown and error handling

Architecture:
- Async MQTT client for non-blocking communication
- Message queue for parallel input/output handling
- Emotion state tracking and visualization
- Structured response parsing with fallback
"""

import argparse
import asyncio
import json
import logging
import signal
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional, Dict, Any

try:
    from aiomqtt import Client as MQTTClient
except ImportError:
    print("ERROR: aiomqtt is required. Install with: pip install aiomqtt")
    sys.exit(1)

try:
    from colorama import Fore, Back, Style, init
except ImportError:
    print("ERROR: colorama is required. Install with: pip install colorama")
    sys.exit(1)

# Initialize colorama for cross-platform colored output
init(autoreset=True)

# ============================================================================
# CONFIGURATION
# ============================================================================

MQTT_BROKER = "localhost"
MQTT_PORT = 1883
MQTT_TOPIC_INPUT = "sentient/conversation/input"
MQTT_TOPIC_RESPONSE = "sentient/conversation/response"
MQTT_TOPIC_THINKING = "sentient/conversation/thinking"
MQTT_TOPIC_EMOTION = "sentient/emotion/state"

# Logging configuration
LOG_DIR = Path("/var/log/sentient")
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_DIR / "cli.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("SentientCLI")

# ============================================================================
# ENUMS AND DATA CLASSES
# ============================================================================


class EmotionState(str, Enum):
    """Emotion states that Cortana can express"""
    NEUTRAL = "neutral"
    HAPPY = "happy"
    CURIOUS = "curious"
    THOUGHTFUL = "thoughtful"
    CONCERNED = "concerned"
    EXCITED = "excited"
    CONFUSED = "confused"


@dataclass
class EmotionData:
    """Emotion state with intensity"""
    emotion: EmotionState
    intensity: float  # 0.0 - 1.0
    timestamp: float

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EmotionData":
        """Parse emotion data from dict"""
        try:
            return cls(
                emotion=EmotionState(data.get("emotion", "neutral")),
                intensity=float(data.get("intensity", 0.5)),
                timestamp=float(data.get("timestamp", 0))
            )
        except (ValueError, TypeError):
            return cls(EmotionState.NEUTRAL, 0.5, 0)


@dataclass
class ThinkingState:
    """Thinking/deliberation state"""
    is_thinking: bool
    topic: str = ""
    stage: str = ""  # e.g., "analyzing", "reasoning", "formulating"
    confidence: float = 0.0


# ============================================================================
# TERMINAL UTILITIES
# ============================================================================


class Spinner:
    """Simple spinning indicator for thinking state"""

    FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self):
        self.frame_idx = 0
        self.running = False

    def next_frame(self) -> str:
        """Get next spinner frame"""
        frame = self.FRAMES[self.frame_idx % len(self.FRAMES)]
        self.frame_idx += 1
        return frame

    def reset(self):
        """Reset spinner to start"""
        self.frame_idx = 0


def emotion_to_emoji(emotion: EmotionState, intensity: float) -> str:
    """Convert emotion state to emoji"""
    emoji_map = {
        EmotionState.NEUTRAL: "😐",
        EmotionState.HAPPY: "😊",
        EmotionState.CURIOUS: "🤔",
        EmotionState.THOUGHTFUL: "🧠",
        EmotionState.CONCERNED: "😟",
        EmotionState.EXCITED: "🤩",
        EmotionState.CONFUSED: "❓",
    }
    return emoji_map.get(emotion, "😐")


def format_emotion(emotion_data: Optional[EmotionData]) -> str:
    """Format emotion state for display"""
    if not emotion_data:
        return ""

    emoji = emotion_to_emoji(emotion_data.emotion, emotion_data.intensity)
    intensity_bar = "▓" * int(emotion_data.intensity * 5)
    intensity_bar += "░" * (5 - int(emotion_data.intensity * 5))

    return f"{emoji} {emotion_data.emotion.value} [{intensity_bar}]"


def format_timestamp() -> str:
    """Format current timestamp for display"""
    return datetime.now().strftime("%H:%M:%S")


def print_user_message(text: str):
    """Print user message with styling"""
    timestamp = format_timestamp()
    print(f"{Fore.CYAN}[{timestamp}] {Style.BRIGHT}You:{Style.RESET_ALL} {text}")


def print_assistant_message(text: str, emotion: Optional[EmotionData] = None):
    """Print assistant response with styling and emotion"""
    timestamp = format_timestamp()
    emotion_str = format_emotion(emotion)

    if emotion_str:
        emotion_str = f" {emotion_str}"

    print(
        f"{Fore.GREEN}[{timestamp}] {Style.BRIGHT}Cortana{emotion_str}:{Style.RESET_ALL}\n"
        f"{text}\n"
    )


def print_thinking_indicator(
    thinking: ThinkingState, spinner: Spinner
) -> str:
    """Print thinking indicator during deliberation"""
    if not thinking.is_thinking:
        return ""

    spinner_char = spinner.next_frame()
    stage_str = f" ({thinking.stage})" if thinking.stage else ""
    topic_str = f" about {thinking.topic}" if thinking.topic else ""

    return f"{Fore.YELLOW}{spinner_char} Thinking{stage_str}{topic_str}...{Style.RESET_ALL}"


def print_debug_message(message: str):
    """Print debug message for internal deliberation"""
    timestamp = format_timestamp()
    print(
        f"{Fore.MAGENTA}[{timestamp}] [DEBUG] {message}{Style.RESET_ALL}"
    )


def print_error_message(message: str):
    """Print error message"""
    timestamp = format_timestamp()
    print(
        f"{Fore.RED}[{timestamp}] {Style.BRIGHT}ERROR:{Style.RESET_ALL} {message}"
    )


def print_info_message(message: str):
    """Print info message"""
    timestamp = format_timestamp()
    print(
        f"{Fore.BLUE}[{timestamp}] {Style.BRIGHT}INFO:{Style.RESET_ALL} {message}"
    )


# ============================================================================
# MQTT CLIENT WRAPPER
# ============================================================================


class SentientMQTTClient:
    """Async MQTT client for Sentient Core communication"""

    def __init__(
        self,
        broker: str = MQTT_BROKER,
        port: int = MQTT_PORT,
        debug: bool = False
    ):
        self.broker = broker
        self.port = port
        self.debug = debug
        self.client = None
        self.running = False
        self.current_emotion: Optional[EmotionData] = None
        self.current_thinking: ThinkingState = ThinkingState(False)

        # Message queues
        self.response_queue: asyncio.Queue = asyncio.Queue()
        self.thinking_queue: asyncio.Queue = asyncio.Queue()
        self.emotion_queue: asyncio.Queue = asyncio.Queue()

    async def connect(self) -> bool:
        """Connect to MQTT broker"""
        try:
            self.client = MQTTClient(self.broker, self.port)
            await self.client.__aenter__()
            self.running = True

            if self.debug:
                print_debug_message(
                    f"Connected to MQTT broker at {self.broker}:{self.port}"
                )

            return True

        except Exception as e:
            print_error_message(f"Failed to connect to MQTT broker: {e}")
            return False

    async def disconnect(self):
        """Disconnect from MQTT broker"""
        if self.client:
            try:
                await self.client.__aexit__(None, None, None)
                self.running = False
                if self.debug:
                    print_debug_message("Disconnected from MQTT broker")
            except Exception as e:
                logger.error(f"Error disconnecting: {e}")

    async def publish_input(self, message: str) -> bool:
        """Publish user input to conversation service"""
        if not self.running or not self.client:
            return False

        try:
            payload = {
                "message": message,
                "timestamp": datetime.now().isoformat(),
                "source": "cli"
            }

            await self.client.publish(
                MQTT_TOPIC_INPUT,
                json.dumps(payload)
            )

            if self.debug:
                print_debug_message(f"Published to {MQTT_TOPIC_INPUT}: {message}")

            return True

        except Exception as e:
            print_error_message(f"Failed to publish message: {e}")
            return False

    async def subscribe_to_responses(self):
        """Subscribe to response topics and handle messages"""
        if not self.running or not self.client:
            return

        try:
            # Subscribe to all response-related topics
            topics = [
                MQTT_TOPIC_RESPONSE,
                MQTT_TOPIC_THINKING,
                MQTT_TOPIC_EMOTION
            ]

            async with self.client.messages() as messages:
                await self.client.subscribe(MQTT_TOPIC_RESPONSE)
                await self.client.subscribe(MQTT_TOPIC_THINKING)
                await self.client.subscribe(MQTT_TOPIC_EMOTION)

                if self.debug:
                    print_debug_message(
                        f"Subscribed to: {', '.join(topics)}"
                    )

                async for message in messages:
                    await self._handle_message(
                        message.topic.value,
                        message.payload.decode()
                    )

        except asyncio.CancelledError:
            if self.debug:
                print_debug_message("Subscription cancelled")
        except Exception as e:
            print_error_message(f"Subscription error: {e}")

    async def _handle_message(self, topic: str, payload: str):
        """Route incoming message to appropriate handler"""
        try:
            data = json.loads(payload)

            if topic == MQTT_TOPIC_RESPONSE:
                await self.response_queue.put(data)
            elif topic == MQTT_TOPIC_THINKING:
                await self.thinking_queue.put(data)
            elif topic == MQTT_TOPIC_EMOTION:
                await self.emotion_queue.put(data)

        except json.JSONDecodeError:
            # Handle non-JSON payloads
            if topic == MQTT_TOPIC_RESPONSE:
                await self.response_queue.put({"message": payload})

        except Exception as e:
            if self.debug:
                print_debug_message(f"Error handling message from {topic}: {e}")

    async def get_response(self, timeout: float = 30.0) -> Optional[str]:
        """Get next response from queue"""
        try:
            data = await asyncio.wait_for(
                self.response_queue.get(),
                timeout=timeout
            )

            # Extract message from response
            if isinstance(data, dict):
                return data.get("message") or data.get("response") or str(data)

            return str(data)

        except asyncio.TimeoutError:
            return None

    async def get_thinking(self) -> Optional[ThinkingState]:
        """Get next thinking state from queue"""
        try:
            data = await asyncio.wait_for(
                self.thinking_queue.get(),
                timeout=0.1
            )

            # Parse thinking state
            if isinstance(data, dict):
                self.current_thinking = ThinkingState(
                    is_thinking=data.get("is_thinking", False),
                    topic=data.get("topic", ""),
                    stage=data.get("stage", ""),
                    confidence=float(data.get("confidence", 0.0))
                )

            return self.current_thinking

        except asyncio.TimeoutError:
            return None

    async def get_emotion(self) -> Optional[EmotionData]:
        """Get next emotion state from queue"""
        try:
            data = await asyncio.wait_for(
                self.emotion_queue.get(),
                timeout=0.1
            )

            if isinstance(data, dict):
                self.current_emotion = EmotionData.from_dict(data)

            return self.current_emotion

        except asyncio.TimeoutError:
            return None


# ============================================================================
# MAIN CLI APPLICATION
# ============================================================================


class SentientCLI:
    """Main CLI application"""

    def __init__(self, debug: bool = False):
        self.debug = debug
        self.mqtt_client = SentientMQTTClient(debug=debug)
        self.running = False
        self.spinner = Spinner()
        self.current_emotion: Optional[EmotionData] = None

        # Setup signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """Handle Ctrl+C and other signals"""
        print("\n")
        print_info_message("Shutting down...")
        self.running = False
        asyncio.create_task(self.shutdown())

    async def run(self):
        """Main application loop"""
        self.running = True

        # Connect to MQTT
        if not await self.mqtt_client.connect():
            print_error_message("Failed to connect to MQTT broker")
            return

        print_info_message("Connected to Sentient Core")
        print(f"{Style.BRIGHT}Welcome to Cortana Terminal Interface{Style.RESET_ALL}")
        print(f"Type your message and press Enter. Type 'quit' to exit.\n")

        try:
            # Start subscription task (background)
            subscription_task = asyncio.create_task(
                self.mqtt_client.subscribe_to_responses()
            )

            # Start input loop
            input_task = asyncio.create_task(self._input_loop())

            # Wait for either to complete (subscription runs forever, input ends on quit)
            done, pending = await asyncio.wait(
                [subscription_task, input_task],
                return_when=asyncio.FIRST_COMPLETED
            )

            # Cancel remaining tasks
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        except Exception as e:
            print_error_message(f"Error in main loop: {e}")

        finally:
            await self.shutdown()

    async def _input_loop(self):
        """Read user input in a non-blocking way"""
        loop = asyncio.get_event_loop()

        while self.running:
            try:
                # Run input reading in executor (thread pool) to avoid blocking
                user_input = await loop.run_in_executor(
                    None,
                    lambda: input(f"{Fore.CYAN}You:{Style.RESET_ALL} ").strip()
                )

                if not user_input:
                    continue

                if user_input.lower() in ("quit", "exit", "bye"):
                    self.running = False
                    break

                # Print user message
                print_user_message(user_input)

                # Publish to MQTT
                if await self.mqtt_client.publish_input(user_input):
                    await self._wait_for_response()
                else:
                    print_error_message("Failed to send message")

            except EOFError:
                # End of stdin
                break
            except Exception as e:
                print_error_message(f"Input error: {e}")
                continue

    async def _wait_for_response(self, timeout: float = 30.0):
        """Wait for response with thinking indicator"""
        thinking_loop_task = None
        response_start_time = asyncio.get_event_loop().time()

        try:
            # Start thinking indicator loop
            thinking_loop_task = asyncio.create_task(
                self._thinking_indicator_loop()
            )

            # Wait for response
            response = await self.mqtt_client.get_response(timeout=timeout)

            # Cancel thinking indicator
            if thinking_loop_task:
                thinking_loop_task.cancel()
                try:
                    await thinking_loop_task
                except asyncio.CancelledError:
                    pass

            if response:
                # Get current emotion
                await self.mqtt_client.get_emotion()
                emotion = self.mqtt_client.current_emotion

                # Display response
                print()
                print_assistant_message(response, emotion)

            else:
                print()
                print_error_message(
                    "No response received (timeout after 30s). "
                    "Check if conversation service is running."
                )

        except Exception as e:
            if thinking_loop_task:
                thinking_loop_task.cancel()
            print_error_message(f"Error waiting for response: {e}")

    async def _thinking_indicator_loop(self):
        """Display thinking indicator while waiting"""
        frame_count = 0

        try:
            while True:
                # Poll for thinking state
                await self.mqtt_client.get_thinking()
                thinking = self.mqtt_client.current_thinking

                # Every other frame, check for thinking state
                if frame_count % 2 == 0 and (
                    thinking.is_thinking or self.debug
                ):
                    indicator = print_thinking_indicator(thinking, self.spinner)
                    if indicator:
                        print(f"\r{indicator}", end="", flush=True)

                frame_count += 1
                await asyncio.sleep(0.2)  # Update every 200ms

        except asyncio.CancelledError:
            # Clear the thinking indicator on cancel
            print("\r" + " " * 80 + "\r", end="", flush=True)

    async def shutdown(self):
        """Graceful shutdown"""
        self.running = False
        await self.mqtt_client.disconnect()
        print_info_message("Goodbye!")


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================


async def main():
    """Entry point"""
    parser = argparse.ArgumentParser(
        description="Sentient Core Terminal CLI Interface",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                    # Standard interactive mode
  %(prog)s --debug            # Show internal deliberation
  %(prog)s --host 192.168.1.5 # Connect to remote broker
        """
    )

    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug mode (show MQTT messages and internal deliberation)"
    )

    parser.add_argument(
        "--host",
        default=MQTT_BROKER,
        help=f"MQTT broker hostname (default: {MQTT_BROKER})"
    )

    parser.add_argument(
        "--port",
        type=int,
        default=MQTT_PORT,
        help=f"MQTT broker port (default: {MQTT_PORT})"
    )

    args = parser.parse_args()

    # Create and run CLI
    cli = SentientCLI(debug=args.debug)

    # Override MQTT settings if provided
    cli.mqtt_client.broker = args.host
    cli.mqtt_client.port = args.port

    try:
        await cli.run()
    except KeyboardInterrupt:
        print("\n")
        print_info_message("Interrupted by user")
    except Exception as e:
        print_error_message(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nTerminated")
        sys.exit(0)
    except Exception as e:
        print(f"Fatal error: {e}", file=sys.stderr)
        sys.exit(1)

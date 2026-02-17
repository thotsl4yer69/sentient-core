"""
System tools for Cortana AI - comprehensive system access.

Provides Cortana with the ability to:
- Check system resources (memory, disk, GPU, CPU)
- Manage services (status, restart, logs)
- Network awareness (devices, interfaces, connectivity)
- Self-awareness (own config, model, uptime, identity)
- Memory system access (search, recall, stats, core memory)
- Avatar control (mood, attention, expressions)
- Log viewing (service logs, system errors)
"""

import asyncio
import json
import logging
import re
import time
from datetime import datetime
from typing import Optional, Tuple, List

logger = logging.getLogger(__name__)


class SystemTools:
    """Comprehensive system access with intent detection and safe execution."""

    # =================================================================
    # COMMAND WHITELIST
    # =================================================================

    COMMANDS = {
        # --- System Info ---
        "system_info": {
            "uptime": ["uptime"],
            "memory": ["free", "-m"],
            "disk": ["df", "-h", "/"],
            "cpu_info": ["cat", "/proc/loadavg"],
            "gpu_status": None,  # handled by _get_gpu_info
            "temperature": None,  # handled by _get_temperatures
        },

        # --- Service Status ---
        "service_status": {
            "list_services": ["systemctl", "list-units", "sentient-*.service", "--all", "--no-pager"],
            "check_service": None,  # handled dynamically
        },

        # --- Service Control ---
        "service_control": {},  # restart handled by _execute_service_control

        # --- Network ---
        "network": {
            "hostname": ["hostname"],
            "ip_address": ["hostname", "-I"],
            "ping_test": ["ping", "-c", "1", "-W", "2", "8.8.8.8"],
            "interfaces": ["ip", "-br", "addr", "show"],
            "connections": ["ss", "-tunp"],
            "routes": ["ip", "route", "show"],
            "wifi_info": None,  # handled by _get_wifi_info
            "dns_servers": ["cat", "/etc/resolv.conf"],
        },

        # --- Network Scanning ---
        "network_scan": {
            "connected_devices": None,  # handled by _get_connected_devices
            "open_ports": ["ss", "-tlnp"],
            "local_scan": None,  # handled by _scan_network
        },

        # --- Ollama ---
        "ollama": {
            "list_models": ["curl", "-s", "localhost:11434/api/tags"],
            "loaded_model": ["curl", "-s", "localhost:11434/api/ps"],
        },

        # --- Self Diagnostic ---
        "self_diagnostic": {
            "health_memory": ["curl", "-s", "localhost:8001/health"],
            "health_contemplation": ["curl", "-s", "localhost:8002/health"],
            "health_perception": ["curl", "-s", "localhost:8003/health"],
            "health_conversation": ["curl", "-s", "localhost:3001/health"],
        },

        # --- Process ---
        "process": {
            "top_memory": ["ps", "aux", "--sort=-%mem"],
            "top_cpu": ["ps", "aux", "--sort=-%cpu"],
        },

        # --- Self Awareness ---
        "self_awareness": {
            "whoami": None,          # handled by _get_self_info
            "my_config": None,       # handled by _get_config
            "my_model": None,        # handled by _get_model_info
            "my_uptime": None,       # handled by _get_service_uptimes
            "my_version": None,      # handled by _get_version
            "my_mood": None,         # handled by _get_my_mood
        },

        # --- Memory System ---
        "memory_tools": {
            "memory_stats": ["curl", "-s", "localhost:8001/stats"],
            "core_memory": ["curl", "-s", "localhost:8001/core"],
            "recent_context": ["curl", "-s", "localhost:8001/context?limit=5"],
            "search_memory": None,   # handled by _search_memory
            "store_memory": None,    # handled by _store_core_memory
        },

        # --- Avatar Control ---
        "avatar_control": {
            "set_mood": None,        # handled by _set_avatar_mood
            "set_attention": None,   # handled by _set_avatar_attention
            "avatar_status": None,   # handled by _get_avatar_status
        },

        # --- Log Viewer ---
        "log_viewer": {
            "system_errors": ["journalctl", "--no-pager", "-n", "15", "--priority=err", "--since", "1 hour ago"],
            "service_log": None,     # handled by _get_service_log
            "dmesg": ["dmesg", "--time-format=reltime", "-n", "err"],
        },

        # --- Weather ---
        "weather": {
            "current_weather": None,  # handled by _get_weather
        },

        # --- Reminders ---
        "reminders": {
            "set_reminder": None,     # handled by _set_reminder
            "list_reminders": None,   # handled by _list_reminders
            "cancel_reminder": None,  # handled by _cancel_reminder
        },
    }

    # =================================================================
    # INTENT DETECTION PATTERNS
    # =================================================================

    # NOTE: Order matters! More specific categories MUST come before generic ones.
    # e.g. memory_tools before system_info (both match "memory"),
    #      process before system_info (both match "cpu"/"memory"),
    #      self_awareness before ollama (both match "model").
    INTENT_PATTERNS = {
        # --- Most specific first ---
        "memory_tools": {
            "store_memory": [r"remember\s+that\b", r"store.*memory", r"save.*memory",
                             r"don't.*forget", r"keep.*in.*mind"],
            "search_memory": [r"remember\s+when\b", r"recall\s+when\b", r"search.*memor",
                              r"do.*you.*remember", r"find.*memor", r"in.*your.*memor"],
            "memory_stats": [r"memory.*stat", r"how.*many.*memories", r"memory.*count",
                             r"memory.*size"],
            "core_memory": [r"core.*memory", r"what.*know.*about.*me", r"what.*remember.*about.*me",
                            r"permanent.*memory"],
            "recent_context": [r"recent.*conversation", r"conversation.*history",
                               r"what.*we.*talk", r"recent.*memory", r"recent.*chat"],
        },
        "self_awareness": {
            "whoami": [r"who are you", r"what are you", r"about yourself", r"tell.*about.*you",
                       r"introduce yourself", r"your.*name"],
            "my_config": [r"(your|my).*config", r"configuration", r"settings",
                          r"show.*config", r"current.*config"],
            "my_model": [r"model.*using", r"what.*brain", r"what.*llm",
                         r"what.*ai\b", r"your.*model"],
            "my_uptime": [r"your.*uptime", r"service.*uptime", r"how long.*been.*running",
                          r"when.*start", r"service.*running.*since"],
            "my_version": [r"(your|what).*version", r"version.*info"],
            "my_mood": [r"how.*feel", r"how.*doing", r"are you.*ok", r"you.*alright",
                        r"what.*your.*mood", r"current.*mood", r"current.*emotion",
                        r"what.*mood", r"emotional.*state", r"feeling.*today"],
        },
        "avatar_control": {
            "set_mood": [r"(set|change|make).*mood", r"(set|change|make).*emotion",
                         r"(set|change|make).*expression", r"feel.*happy",
                         r"feel.*sad", r"look.*happy", r"look.*alert",
                         r"cheer.*up", r"be.*happy", r"smile"],
            "set_attention": [r"look.*(left|right|up|down|at me|away|center|at the|at\s+\w+)",
                              r"attention.*(left|right|up|down|here|away)",
                              r"look\s+at\b"],
            "avatar_status": [r"avatar.*status", r"how.*look", r"avatar.*state",
                              r"avatar.*info"],
        },
        "process": {
            "top_cpu": [r"top.*cpu", r"cpu.*process", r"most.*cpu",
                        r"what.*using.*cpu", r"cpu.*hog"],
            "top_memory": [r"top.*process", r"memory.*process", r"most.*memory",
                           r"what.*using.*memory", r"memory.*hog"],
        },
        "self_diagnostic": {
            "health_memory": [r"health.*memory", r"memory.*health", r"check.*memory.*service"],
            "health_contemplation": [r"health.*contemplation", r"contemplation.*health"],
            "health_perception": [r"health.*perception", r"perception.*health"],
            "health_conversation": [r"health.*conversation", r"conversation.*health"],
        },
        "log_viewer": {
            "system_errors": [r"system.*error", r"error.*log", r"recent.*error",
                              r"any.*error", r"what.*wrong"],
            "service_log": [r"log.*for\s+\w+", r"show.*log", r"check.*log"],
            "dmesg": [r"\bdmesg\b", r"kernel.*message", r"kernel.*log",
                      r"hardware.*error"],
        },
        # --- Then broader categories ---
        "service_status": {
            "list_services": [r"list.*service", r"what.*service", r"show.*service", r"all.*service"],
            "check_service": [r"check.*service", r"status.*service", r"service.*status", r"is.*running"],
        },
        "service_control": {
            "restart_service": [r"restart\s+\w+", r"reboot.*service"],
        },
        "network": {
            "hostname": [r"\bhostname\b", r"what.*hostname", r"machine.*name", r"computer.*name"],
            "ip_address": [r"\bip\s", r"ip address", r"network address", r"local.*ip", r"my.*ip"],
            "ping_test": [r"\bping\b", r"network.*test", r"connectivity", r"internet.*work"],
            "interfaces": [r"network.*interface", r"net.*interface", r"show.*interface"],
            "connections": [r"active.*connection", r"open.*connection", r"network.*connection"],
            "routes": [r"routing.*table", r"network.*route", r"default.*gateway", r"gateway"],
            "wifi_info": [r"\bwifi\b", r"\bwi-fi\b", r"wireless", r"ssid", r"signal.*strength"],
            "dns_servers": [r"\bdns\b", r"name.*server", r"dns.*server", r"resolver"],
        },
        "network_scan": {
            "connected_devices": [r"connected.*device", r"devices.*network", r"who.*network",
                                  r"what.*connected", r"network.*device", r"arp"],
            "open_ports": [r"open.*port", r"listening.*port", r"port.*open", r"what.*port"],
            "local_scan": [r"scan.*network", r"network.*scan", r"nmap", r"discover.*device"],
        },
        "ollama": {
            "list_models": [r"list.*model", r"available.*model", r"model.*available",
                            r"show.*model", r"installed.*model", r"what.*models"],
            "loaded_model": [r"loaded.*model", r"current.*model", r"running.*model",
                             r"active.*model", r"which.*model"],
        },
        "reminders": {
            "set_reminder": [r"remind\s+me\b", r"set.*reminder", r"set.*timer", r"alert.*me.*in\b",
                             r"notify.*me.*in\b", r"wake.*me.*in\b", r"in\s+\d+\s*(min|hour|sec)",
                             r"remind.*at\s+\d", r"timer\s+for\b"],
            "list_reminders": [r"list.*reminder", r"show.*reminder", r"my.*reminder",
                               r"what.*remind", r"pending.*reminder", r"active.*reminder",
                               r"upcoming.*reminder"],
            "cancel_reminder": [r"cancel.*reminder", r"delete.*reminder", r"remove.*reminder",
                                r"clear.*reminder", r"stop.*reminder"],
        },
        "weather": {
            "current_weather": [r"\bweather\b", r"temperature.*outside", r"how.*cold", r"how.*hot",
                                r"outside.*temp", r"forecast", r"rain", r"sunny", r"cloudy"],
        },
        # --- Most generic last ---
        "system_info": {
            "uptime": [r"\buptime\b", r"how long.*running", r"system.*up"],
            "memory": [r"check.*memory", r"free.*memory", r"how much.*memory",
                       r"\bram\b", r"memory.*usage"],
            "disk": [r"\bdisk\b", r"disk space", r"storage", r"how much.*space"],
            "cpu_info": [r"cpu\s*(info|load|usage)", r"processor", r"load average",
                         r"\bcpu\b"],
            "gpu_status": [r"gpu\s*(status|info|load|temp)", r"graphics", r"tegra",
                           r"\bgpu\b"],
            "temperature": [r"\btemperature\b", r"\btemp\b", r"how hot", r"thermal"],
        },
    }

    # Combined diagnostic patterns
    COMBINED_PATTERNS = {
        "self_diagnostic": [
            r"health.*check",
            r"diagnostic",
            r"system.*health",
            r"check.*all.*service",
            r"status.*all",
            r"run.*diagnostic",
            r"self.*check",
        ],
        "full_status": [
            r"full.*status",
            r"system.*report",
            r"status.*report",
            r"overview",
            r"system.*overview",
            r"sitrep",
        ],
    }

    # Emotion name mapping for avatar mood
    EMOTION_MAP = {
        "happy": "happy", "joy": "happy", "glad": "happy", "cheerful": "happy",
        "amused": "amused", "funny": "amused", "laugh": "amused",
        "concerned": "concerned", "worried": "concerned", "sad": "concerned",
        "focused": "focused", "concentrate": "focused", "serious": "focused",
        "curious": "curious", "interested": "curious", "wonder": "curious",
        "protective": "protective", "guard": "protective", "defend": "protective",
        "affectionate": "affectionate", "love": "affectionate", "warm": "affectionate",
        "thoughtful": "thoughtful", "think": "thoughtful", "ponder": "thoughtful",
        "alert": "alert", "attention": "alert", "wake": "alert",
        "neutral": "neutral", "calm": "neutral", "normal": "neutral", "default": "neutral",
    }

    # Services that can be restarted
    ALLOWED_SERVICES = {
        'contemplation', 'conversation', 'memory', 'perception',
        'web-chat', 'avatar-bridge', 'wake-word', 'scheduler',
        'notification', 'rf-detection', 'piper-tts', 'whisper-stt',
        'voice', 'proactive',
    }

    # =================================================================
    # INTENT DETECTION
    # =================================================================

    def detect_intent(self, user_message: str) -> Optional[Tuple[str, str]]:
        """Detect if user message is requesting a system operation."""
        msg_lower = user_message.lower()

        # Check combined patterns first
        for category, patterns in self.COMBINED_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, msg_lower):
                    logger.info(f"Detected combined intent: {category}")
                    return (category, "all")

        # Check specific command patterns
        for category, commands in self.INTENT_PATTERNS.items():
            for command_name, patterns in commands.items():
                for pattern in patterns:
                    if re.search(pattern, msg_lower):
                        logger.info(f"Detected intent: {category}/{command_name}")
                        return (category, command_name)

        return None

    # =================================================================
    # EXECUTION
    # =================================================================

    async def execute(self, category: str, command: str, user_message: str = "") -> str:
        """Execute a system tool command."""

        # --- Combined / multi-command handlers ---
        if category == "self_diagnostic" and command == "all":
            return await self._run_all_health_checks()

        if category == "full_status" and command == "all":
            return await self._run_full_status()

        # --- Special handlers ---
        handlers = {
            ("system_info", "gpu_status"): lambda: self._get_gpu_info(),
            ("system_info", "temperature"): lambda: self._get_temperatures(),
            ("network", "wifi_info"): lambda: self._get_wifi_info(),
            ("network_scan", "connected_devices"): lambda: self._get_connected_devices(),
            ("network_scan", "local_scan"): lambda: self._scan_network(),
            ("self_awareness", "whoami"): lambda: self._get_self_info(),
            ("self_awareness", "my_config"): lambda: self._get_config(),
            ("self_awareness", "my_model"): lambda: self._get_model_info(),
            ("self_awareness", "my_uptime"): lambda: self._get_service_uptimes(),
            ("self_awareness", "my_version"): lambda: self._get_version(),
            ("self_awareness", "my_mood"): lambda: self._get_my_mood(),
            ("memory_tools", "search_memory"): lambda: self._search_memory(user_message),
            ("memory_tools", "store_memory"): lambda: self._store_core_memory(user_message),
            ("avatar_control", "set_mood"): lambda: self._set_avatar_mood(user_message),
            ("avatar_control", "set_attention"): lambda: self._set_avatar_attention(user_message),
            ("avatar_control", "avatar_status"): lambda: self._get_avatar_status(),
            ("log_viewer", "service_log"): lambda: self._get_service_log(user_message),
            ("service_status", "check_service"): lambda: self._check_specific_service(user_message),
            ("weather", "current_weather"): lambda: self._get_weather(),
            ("reminders", "set_reminder"): lambda: self._set_reminder(user_message),
            ("reminders", "list_reminders"): lambda: self._list_reminders(),
            ("reminders", "cancel_reminder"): lambda: self._cancel_reminder(user_message),
        }

        handler = handlers.get((category, command))
        if handler:
            return await handler()

        # --- Service control ---
        if category == "service_control":
            return await self._execute_service_control(command, user_message)

        # --- Standard whitelisted commands ---
        if category not in self.COMMANDS:
            logger.warning(f"Unknown category: {category}")
            return f"Error: Unknown command category '{category}'"

        if command not in self.COMMANDS[category]:
            logger.warning(f"Unknown command: {command} in {category}")
            return f"Error: Unknown command '{command}' in category '{category}'"

        cmd_template = self.COMMANDS[category][command]
        if cmd_template is None:
            return "Command not available."

        return await self._run_command(cmd_template)

    # =================================================================
    # COMBINED HANDLERS
    # =================================================================

    async def _run_all_health_checks(self) -> str:
        """Run all health check endpoints and aggregate results."""
        health_commands = self.COMMANDS["self_diagnostic"]
        results = []

        for service_name, cmd in health_commands.items():
            service_display = service_name.replace("health_", "")
            try:
                output = await self._run_command(cmd, timeout=3)
                status = "OK" if "ok" in output.lower() or "healthy" in output.lower() else "?"
                results.append(f"{status} {service_display}: {output.strip()}")
            except Exception as e:
                results.append(f"FAIL {service_display}: {str(e)}")

        return "\n".join(results)

    async def _run_full_status(self) -> str:
        """Run a comprehensive system status report."""
        parts = []

        # System info
        uptime = await self._run_command(["uptime"], timeout=3)
        parts.append(f"Uptime: {uptime}")

        memory = await self._run_command(["free", "-m"], timeout=3)
        parts.append(f"Memory:\n{memory}")

        disk = await self._run_command(["df", "-h", "/"], timeout=3)
        parts.append(f"Disk:\n{disk}")

        gpu = await self._get_gpu_info()
        parts.append(f"GPU: {gpu}")

        # Network
        ip_addr = await self._run_command(["hostname", "-I"], timeout=3)
        parts.append(f"IP: {ip_addr}")

        # Services
        services = await self._run_command(
            ["systemctl", "list-units", "sentient-*.service", "--all", "--no-pager"],
            timeout=5
        )
        parts.append(f"Services:\n{services}")

        # Model
        model = await self._run_command(["curl", "-s", "localhost:11434/api/ps"], timeout=3)
        parts.append(f"Ollama: {model}")

        return "\n\n".join(parts)

    # =================================================================
    # SELF-AWARENESS HANDLERS
    # =================================================================

    async def _get_self_info(self) -> str:
        """Get Cortana's self-description and system identity — conversational format."""
        uptime = await self._run_command(["uptime", "-p"], timeout=3)
        model_info = await self._run_command(["curl", "-s", "localhost:11434/api/ps"], timeout=3)

        # Parse model name
        model_name = "unknown"
        try:
            data = json.loads(model_info)
            models = data.get("models", [])
            if models:
                model_name = models[0].get("name", "unknown")
        except (json.JSONDecodeError, KeyError):
            pass

        uptime_clean = uptime.replace("up ", "").strip() if uptime else "unknown"
        return (
            f"You are Cortana, an AI running on Jack's Jetson Orin Nano (ARM64, 8GB RAM, GPU). "
            f"Your brain is {model_name} via Ollama. You've been running for {uptime_clean}. "
            f"You have 10 microservices: conversation, memory, contemplation, perception, avatar, "
            f"proactive behavior, TTS, STT, wake-word detection, and a web chat interface."
        )

    async def _get_config(self) -> str:
        """Read Cortana's configuration file."""
        try:
            output = await self._run_command(["cat", "/opt/sentient-core/config/cortana.toml"], timeout=3)
            return f"Current configuration:\n{output}"
        except Exception as e:
            return f"Error reading config: {e}"

    async def _get_model_info(self) -> str:
        """Get detailed info about the currently loaded AI model."""
        model_info = await self._run_command(["curl", "-s", "localhost:11434/api/ps"], timeout=3)
        model_list = await self._run_command(["curl", "-s", "localhost:11434/api/tags"], timeout=3)

        parts = [f"Loaded model:\n{model_info}"]

        try:
            data = json.loads(model_list)
            models = data.get("models", [])
            if models:
                parts.append("Available models:")
                for m in models:
                    name = m.get("name", "?")
                    size_bytes = m.get("size", 0)
                    size_gb = size_bytes / (1024**3)
                    parts.append(f"  - {name} ({size_gb:.1f}GB)")
        except (json.JSONDecodeError, KeyError):
            pass

        return "\n".join(parts)

    async def _get_service_uptimes(self) -> str:
        """Get uptime for all sentient services."""
        output = await self._run_command(
            ["systemctl", "list-units", "sentient-*.service", "--all", "--no-pager",
             "--plain", "--no-legend"],
            timeout=5
        )
        services = []
        for line in output.strip().split("\n"):
            parts = line.strip().split()
            if not parts:
                continue
            svc_name = parts[0]
            # Get uptime for active services
            active_since = await self._run_command(
                ["systemctl", "show", svc_name, "--property=ActiveEnterTimestamp", "--no-pager"],
                timeout=3
            )
            services.append(f"{svc_name}: {active_since}")

        return "\n".join(services) if services else "No services found."

    async def _get_version(self) -> str:
        """Get Sentient Core version info."""
        parts = []

        # Check for version file or git info
        version = await self._run_command(["cat", "/opt/sentient-core/VERSION"], timeout=2)
        if not version.startswith("Error"):
            parts.append(f"Sentient Core: v{version.strip()}")
        else:
            # Try pip package version
            pip_info = await self._run_command(
                ["python3", "-c", "import sentient; print(getattr(sentient, '__version__', 'dev'))"],
                timeout=3
            )
            if not pip_info.startswith("Error"):
                parts.append(f"Sentient Core: {pip_info.strip()}")
            else:
                parts.append("Sentient Core: v2 (development)")

        # Python version
        py_ver = await self._run_command(["python3", "--version"], timeout=3)
        parts.append(f"Python: {py_ver}")

        # Ollama version
        ollama_ver = await self._run_command(["ollama", "--version"], timeout=3)
        parts.append(f"Ollama: {ollama_ver}")

        return "\n".join(parts) if parts else "Version info unavailable."

    async def _get_my_mood(self) -> str:
        """Read Cortana's current mood from Redis and return natural description."""
        try:
            result = await self._run_command(
                ["redis-cli", "GET", "sentient:cortana:mood"],
                timeout=3
            )
            if result and result.strip() and result.strip() != "(nil)":
                import json as _json
                mood_data = _json.loads(result.strip())
                emotion = mood_data.get("emotion", "neutral")
                valence = mood_data.get("valence", 0.0)
                intensity = mood_data.get("intensity", 0.3)
                recent = mood_data.get("recent", [])

                # Build natural description
                if intensity > 0.7:
                    level = "very"
                elif intensity > 0.5:
                    level = "somewhat"
                elif intensity > 0.35:
                    level = "mildly"
                else:
                    level = ""

                mood_words = {
                    'joy': 'happy and upbeat',
                    'curiosity': 'curious and engaged',
                    'affection': 'warm and caring',
                    'sadness': 'a bit down',
                    'anger': 'irritated',
                    'surprise': 'surprised',
                    'fear': 'on edge',
                    'confidence': 'confident and sharp',
                    'playful': 'in a playful mood',
                    'neutral': 'calm and steady',
                }

                desc = mood_words.get(emotion, emotion)
                if level and emotion != 'neutral':
                    desc = f"{level} {desc}"

                # Mood trend from recent emotions
                trend = ""
                if len(recent) >= 3:
                    if len(set(recent[-3:])) == 1 and recent[-1] != 'neutral':
                        trend = f" This has been building over the last few conversations."
                    elif recent[-1] != recent[0] and len(recent) >= 2:
                        prev = mood_words.get(recent[-2], recent[-2])
                        trend = f" Shifted from {prev} recently."

                # Valence summary
                if valence > 0.5:
                    vibe = "Overall feeling positive."
                elif valence > 0.1:
                    vibe = "Leaning positive."
                elif valence < -0.3:
                    vibe = "Not my best moment."
                else:
                    vibe = ""

                parts = [f"Currently feeling {desc}."]
                if trend:
                    parts.append(trend.strip())
                if vibe:
                    parts.append(vibe)

                return " ".join(parts)
            else:
                return "Feeling neutral — no strong emotions right now. Just steady."
        except Exception as e:
            logger.debug(f"Failed to read mood: {e}")
            return "I'm not sure how I'm feeling right now — my emotional tracking seems offline."

    # =================================================================
    # GPU / THERMAL HANDLERS
    # =================================================================

    async def _get_gpu_info(self) -> str:
        """Get Jetson GPU info from sysfs."""
        parts = []
        try:
            load = await self._run_command(
                ['cat', '/sys/devices/platform/bus@0/17000000.gpu/load'])
            if load and not load.startswith('Error'):
                parts.append(f"GPU Load: {int(load)/10:.0f}%")
        except Exception:
            pass
        try:
            temp = await self._run_command(
                ['cat', '/sys/class/thermal/thermal_zone1/temp'])
            if temp and not temp.startswith('Error'):
                parts.append(f"GPU Temp: {int(temp)/1000:.1f}C")
        except Exception:
            pass
        mem = await self._run_command(['free', '-m'])
        parts.append(f"Memory:\n{mem}")
        return "\n".join(parts) if parts else "GPU info unavailable."

    async def _get_temperatures(self) -> str:
        """Get all thermal zone temperatures."""
        parts = []
        for i in range(8):
            try:
                temp = await self._run_command(
                    ['cat', f'/sys/class/thermal/thermal_zone{i}/temp'], timeout=2)
                zone_type = await self._run_command(
                    ['cat', f'/sys/class/thermal/thermal_zone{i}/type'], timeout=2)
                if temp and not temp.startswith('Error'):
                    parts.append(f"{zone_type.strip()}: {int(temp)/1000:.1f}C")
            except Exception:
                break
        return "\n".join(parts) if parts else "Temperature info unavailable."

    # =================================================================
    # NETWORK HANDLERS
    # =================================================================

    async def _get_wifi_info(self) -> str:
        """Get WiFi connection details."""
        parts = []

        # Try iwconfig
        iw = await self._run_command(["iwconfig", "wlP1p1s0"], timeout=3)
        if not iw.startswith("Error"):
            parts.append(iw)
        else:
            # Try iw
            iw2 = await self._run_command(["iw", "dev", "wlP1p1s0", "link"], timeout=3)
            parts.append(iw2)

        # Signal quality
        signal = await self._run_command(["cat", "/proc/net/wireless"], timeout=2)
        if not signal.startswith("Error"):
            parts.append(f"Signal:\n{signal}")

        return "\n".join(parts) if parts else "WiFi info unavailable."

    async def _get_connected_devices(self) -> str:
        """Get devices on the local network via ARP table."""
        arp = await self._run_command(["ip", "neigh", "show"], timeout=5)
        if arp.startswith("Error"):
            # Fallback to arp command
            arp = await self._run_command(["arp", "-a"], timeout=5)
        return f"Connected devices (ARP table):\n{arp}"

    async def _scan_network(self) -> str:
        """Scan local network for devices."""
        # Get local subnet
        route = await self._run_command(
            ["ip", "route", "show", "dev", "wlP1p1s0"], timeout=3)

        # Extract subnet
        subnet = None
        for line in route.split("\n"):
            if "/" in line and "via" not in line:
                subnet = line.split()[0]
                break

        if not subnet:
            subnet = "192.168.1.0/24"

        # Try nmap (quick ping scan)
        nmap_result = await self._run_command(
            ["nmap", "-sn", subnet, "--max-retries=1"], timeout=15)
        if not nmap_result.startswith("Error"):
            return f"Network scan ({subnet}):\n{nmap_result}"

        # Fallback to ARP
        return await self._get_connected_devices()

    # =================================================================
    # MEMORY SYSTEM HANDLERS
    # =================================================================

    async def _search_memory(self, user_message: str) -> str:
        """Search episodic memories via the Memory API."""
        # Extract search query from user message
        query = user_message.lower()
        # Remove common prefixes
        for prefix in ["do you remember", "remember when", "recall", "search memory for",
                        "search memories for", "find memory", "search memory"]:
            query = re.sub(rf"^{prefix}\s*", "", query).strip()

        if not query:
            query = user_message

        # Call memory API
        payload = json.dumps({"query": query, "limit": 5, "min_similarity": 0.3})
        result = await self._run_command(
            ["curl", "-s", "--max-time", "25", "-X", "POST", "localhost:8001/recall",
             "-H", "Content-Type: application/json",
             "-d", payload],
            timeout=30
        )

        try:
            data = json.loads(result)
            memories = data.get("memories", [])
            if not memories:
                return f"No memories found matching '{query}'."

            parts = [f"Found {len(memories)} memories matching '{query}':"]
            for i, mem in enumerate(memories, 1):
                interaction = mem.get("interaction", {})
                similarity = mem.get("similarity", 0)
                user_msg = interaction.get("user_msg", "?")
                asst_msg = interaction.get("assistant_msg", "?")
                timestamp = interaction.get("timestamp", "?")
                parts.append(
                    f"\n[{i}] (similarity: {similarity:.0%}, time: {timestamp})\n"
                    f"  User: {user_msg[:100]}\n"
                    f"  Cortana: {asst_msg[:100]}"
                )
            return "\n".join(parts)
        except json.JSONDecodeError:
            return f"Memory search result: {result}"

    async def _store_core_memory(self, user_message: str) -> str:
        """Store something in core memory via the Memory API."""
        msg = user_message.lower()
        # Extract what to remember
        content = msg
        for prefix in ["remember that", "store in memory", "save to memory",
                        "don't forget", "keep in mind", "remember"]:
            content = re.sub(rf"^{prefix}\s*", "", content).strip()

        if not content:
            return "What should I remember?"

        # Store as core memory
        payload = json.dumps({"key": f"user_note_{datetime.now().strftime('%Y%m%d_%H%M%S')}", "value": content})
        result = await self._run_command(
            ["curl", "-s", "-X", "POST", "localhost:8001/core",
             "-H", "Content-Type: application/json",
             "-d", payload],
            timeout=5
        )

        try:
            data = json.loads(result)
            if data.get("status") == "updated":
                return f"Stored in core memory: '{content}'"
            return f"Memory store result: {result}"
        except json.JSONDecodeError:
            return f"Memory store result: {result}"

    # =================================================================
    # AVATAR CONTROL HANDLERS
    # =================================================================

    async def _set_avatar_mood(self, user_message: str) -> str:
        """Set avatar mood/emotion via MQTT."""
        msg = user_message.lower()

        # Find matching emotion
        emotion = None
        for keyword, emotion_name in self.EMOTION_MAP.items():
            if keyword in msg:
                emotion = emotion_name
                break

        if not emotion:
            available = sorted(set(self.EMOTION_MAP.values()))
            return f"Which mood? Available: {', '.join(available)}"

        # Publish via mosquitto_pub
        payload = json.dumps({
            "emotion": emotion,
            "intensity": 0.8,
            "timestamp": datetime.now().timestamp()
        })

        result = await self._run_command(
            ["mosquitto_pub", "-t", "sentient/avatar/expression",
             "-m", payload],
            timeout=3
        )

        if result.startswith("Error"):
            return f"Failed to set mood: {result}"
        return f"Avatar mood set to: {emotion}"

    async def _set_avatar_attention(self, user_message: str) -> str:
        """Set avatar attention/gaze direction via MQTT."""
        msg = user_message.lower()

        # Parse direction
        x, y, focus = 0.0, 0.0, 0.8
        if "left" in msg:
            x = -0.7
        elif "right" in msg:
            x = 0.7
        if "up" in msg:
            y = 0.5
        elif "down" in msg:
            y = -0.5
        if "at me" in msg or "here" in msg or "center" in msg:
            x, y, focus = 0.0, 0.0, 1.0
        if "away" in msg:
            x, focus = 0.8, 0.2

        payload = json.dumps({
            "x": x, "y": y, "focus": focus,
            "timestamp": datetime.now().timestamp()
        })

        result = await self._run_command(
            ["mosquitto_pub", "-t", "sentient/avatar/attention",
             "-m", payload],
            timeout=3
        )

        direction = []
        if x < -0.3:
            direction.append("left")
        elif x > 0.3:
            direction.append("right")
        if y > 0.3:
            direction.append("up")
        elif y < -0.3:
            direction.append("down")
        if not direction:
            direction.append("center")

        if result.startswith("Error"):
            return f"Failed to set attention: {result}"
        return f"Avatar looking: {', '.join(direction)} (focus: {focus:.0%})"

    async def _get_avatar_status(self) -> str:
        """Get current avatar state by checking the bridge service."""
        # We can check the service is running and report available emotions
        bridge_status = await self._run_command(
            ["systemctl", "is-active", "sentient-avatar.service"],
            timeout=3
        )

        emotions = sorted(set(self.EMOTION_MAP.values()))

        return (
            f"Avatar bridge: {bridge_status.strip()}\n"
            f"WebSocket port: 9001\n"
            f"Available emotions: {', '.join(emotions)}\n"
            f"Features: breathing animation, blinking, attention wandering, "
            f"emotion-driven colors, phoneme lip sync"
        )

    # =================================================================
    # LOG VIEWER HANDLERS
    # =================================================================

    async def _get_service_log(self, user_message: str) -> str:
        """Get logs for a specific sentient service."""
        msg = user_message.lower()

        # Try to find service name in message
        service = None
        for svc in sorted(self.ALLOWED_SERVICES, key=len, reverse=True):
            if svc in msg:
                service = svc
                break

        # Also check without hyphens
        if not service:
            for svc in self.ALLOWED_SERVICES:
                if svc.replace("-", "") in msg.replace(" ", ""):
                    service = svc
                    break

        if not service:
            # Default to conversation service
            service = "conversation"

        full_name = f"sentient-{service}.service"
        output = await self._run_command(
            ["journalctl", "-u", full_name, "--no-pager", "-n", "20", "--since", "1 hour ago"],
            timeout=5
        )
        return f"Logs for {full_name}:\n{output}"

    async def _check_specific_service(self, user_message: str) -> str:
        """Check status of a specific service."""
        msg = user_message.lower()

        service = None
        for svc in sorted(self.ALLOWED_SERVICES, key=len, reverse=True):
            if svc in msg:
                service = svc
                break

        if not service:
            return "Which service? Available: " + ", ".join(sorted(self.ALLOWED_SERVICES))

        full_name = f"sentient-{service}.service"
        output = await self._run_command(
            ["systemctl", "status", full_name, "--no-pager"],
            timeout=5
        )
        return output

    # =================================================================
    # WEATHER HANDLER
    # =================================================================

    async def _get_weather(self) -> str:
        """Fetch current weather from wttr.in (free, no API key)."""
        try:
            process = await asyncio.create_subprocess_exec(
                "curl", "-s", "-m", "5",
                "-H", "User-Agent: curl/7.68.0",
                "https://wttr.in/?format=%t|%C|%h|%w",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=8
            )

            if process.returncode != 0:
                return "Weather: Unable to fetch (network error)"

            raw = stdout.decode().strip()
            parts = raw.split("|")
            if len(parts) >= 2:
                temp = parts[0].strip()
                condition = parts[1].strip()
                humidity = parts[2].strip() if len(parts) > 2 else "N/A"
                wind = parts[3].strip() if len(parts) > 3 else "N/A"
                return (
                    f"Temperature: {temp}\n"
                    f"Condition: {condition}\n"
                    f"Humidity: {humidity}\n"
                    f"Wind: {wind}"
                )
            else:
                return f"Weather: {raw}"

        except asyncio.TimeoutError:
            return "Weather: Unable to fetch (timeout)"
        except Exception as e:
            logger.error(f"Weather fetch failed: {e}")
            return f"Weather: Unable to fetch ({e})"

    # =================================================================
    # REMINDER SYSTEM
    # =================================================================

    def _parse_reminder_time(self, text: str) -> Optional[Tuple[float, str]]:
        """
        Parse natural language time expression into (due_timestamp, human_label).
        Supports: "in X minutes/hours/seconds", "at HH:MM", "in half an hour",
                  "in an hour", "in a minute", "tomorrow at HH:MM"
        Returns None if no time expression found.
        """
        import time as _time
        now = _time.time()
        text_lower = text.lower()

        # "in half an hour" / "in half hour"
        if re.search(r'in\s+half\s+(an?\s+)?hour', text_lower):
            return (now + 1800, "in 30 minutes")

        # "in an hour" / "in a minute"
        m = re.search(r'in\s+(an?)\s+(hour|minute|min|second|sec)', text_lower)
        if m:
            unit = m.group(2)
            if unit.startswith('hour'):
                return (now + 3600, "in 1 hour")
            elif unit.startswith('min'):
                return (now + 60, "in 1 minute")
            elif unit.startswith('sec'):
                return (now + 1, "in 1 second")

        # "in X minutes/hours/seconds"
        m = re.search(r'in\s+(\d+)\s*(second|sec|minute|min|hour|hr)s?', text_lower)
        if m:
            amount = int(m.group(1))
            unit = m.group(2)
            if unit.startswith('sec'):
                delta = amount
                label = f"in {amount} second{'s' if amount != 1 else ''}"
            elif unit.startswith('min'):
                delta = amount * 60
                label = f"in {amount} minute{'s' if amount != 1 else ''}"
            elif unit.startswith('hour') or unit.startswith('hr'):
                delta = amount * 3600
                label = f"in {amount} hour{'s' if amount != 1 else ''}"
            else:
                return None
            return (now + delta, label)

        # "at HH:MM" or "at H:MMam/pm" or "at Ham/pm"
        m = re.search(r'at\s+(\d{1,2}):?(\d{2})?\s*(am|pm)?', text_lower)
        if m:
            hour = int(m.group(1))
            minute = int(m.group(2)) if m.group(2) else 0
            ampm = m.group(3)

            if ampm == 'pm' and hour < 12:
                hour += 12
            elif ampm == 'am' and hour == 12:
                hour = 0

            now_dt = datetime.now()
            target = now_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)

            # If target is in the past, schedule for tomorrow
            if target <= now_dt:
                target = target.replace(day=target.day + 1)

            label = f"at {target.strftime('%I:%M %p')}"
            return (target.timestamp(), label)

        return None

    def _extract_reminder_text(self, text: str) -> str:
        """Extract the reminder content from the user message."""
        text_lower = text.lower()
        # Remove common prefixes
        for prefix in [
            r'remind\s+me\s+(in\s+half\s+(an?\s+)?hour\s+)?(in\s+\S+\s+\S+\s+)?(to|about|that)\s+',
            r'remind\s+me\s+(in\s+an?\s+\S+\s+)?(to|about|that)\s+',
            r'set\s+(a\s+)?reminder\s+(in\s+\S+\s+\S+\s+)?(to|about|for|that)\s+',
            r'set\s+(a\s+)?timer\s+for\s+\S+\s+\S+\s+(to|about|for|that)\s+',
            r'remind\s+me\s+at\s+\S+\s*(am|pm)?\s*(to|about|that)\s+',
        ]:
            m = re.search(prefix, text_lower)
            if m:
                return text[m.end():].strip().rstrip('.')

        # Fallback: remove time expressions and common words
        cleaned = re.sub(r'remind\s+me\s+', '', text_lower)
        cleaned = re.sub(r'set\s+(a\s+)?reminder\s+', '', cleaned)
        cleaned = re.sub(r'in\s+\d+\s*(second|sec|minute|min|hour|hr)s?\s*', '', cleaned)
        cleaned = re.sub(r'at\s+\d{1,2}:?\d{0,2}\s*(am|pm)?\s*', '', cleaned)
        cleaned = re.sub(r'^(to|about|that|for)\s+', '', cleaned.strip())
        return cleaned.strip().rstrip('.') or "something"

    async def _set_reminder(self, user_message: str) -> str:
        """Parse and store a reminder in Redis sorted set."""
        parsed_time = self._parse_reminder_time(user_message)
        if not parsed_time:
            return ("I couldn't understand the time. Try:\n"
                    "- 'remind me in 30 minutes to check the build'\n"
                    "- 'remind me at 3pm about the meeting'\n"
                    "- 'set a timer for 1 hour'")

        due_timestamp, time_label = parsed_time
        reminder_text = self._extract_reminder_text(user_message)

        # Store in Redis sorted set (score = due timestamp)
        reminder_id = f"r_{int(due_timestamp)}_{hash(reminder_text) % 10000}"
        reminder_data = json.dumps({
            "id": reminder_id,
            "text": reminder_text,
            "due_at": due_timestamp,
            "due_label": time_label,
            "created_at": time.time(),
            "created_human": datetime.now().strftime("%I:%M %p"),
            "user": "Jack",
        })

        try:
            result = await self._run_command(
                ["redis-cli", "ZADD", "sentient:reminders", str(due_timestamp), reminder_data],
                timeout=3
            )
            due_dt = datetime.fromtimestamp(due_timestamp)
            due_str = due_dt.strftime("%I:%M %p")
            return f"Reminder set: \"{reminder_text}\" — due {time_label} (at {due_str})"
        except Exception as e:
            logger.error(f"Failed to set reminder: {e}")
            return f"Failed to save reminder: {e}"

    async def _list_reminders(self) -> str:
        """List all pending reminders from Redis."""
        try:
            result = await self._run_command(
                ["redis-cli", "ZRANGEBYSCORE", "sentient:reminders", "-inf", "+inf", "WITHSCORES"],
                timeout=3
            )
            if not result or result.strip() == "" or result.strip() == "(empty array)":
                return "No active reminders. Set one with 'remind me in X minutes to...'"

            lines = result.strip().split("\n")
            reminders = []
            now = time.time()
            i = 0
            while i < len(lines) - 1:
                try:
                    data = json.loads(lines[i].strip().strip('"'))
                    score = float(lines[i + 1].strip().strip('"'))
                    remaining = score - now
                    if remaining > 0:
                        if remaining > 3600:
                            time_left = f"{remaining / 3600:.1f}h"
                        elif remaining > 60:
                            time_left = f"{int(remaining / 60)}m"
                        else:
                            time_left = f"{int(remaining)}s"
                        due_str = datetime.fromtimestamp(score).strftime("%I:%M %p")
                        reminders.append(f"- \"{data['text']}\" — due at {due_str} ({time_left} from now)")
                    else:
                        reminders.append(f"- \"{data['text']}\" — OVERDUE")
                except (json.JSONDecodeError, KeyError, ValueError):
                    pass
                i += 2

            if not reminders:
                return "No active reminders."

            return f"Active reminders ({len(reminders)}):\n" + "\n".join(reminders)
        except Exception as e:
            logger.error(f"Failed to list reminders: {e}")
            return f"Error listing reminders: {e}"

    async def _cancel_reminder(self, user_message: str) -> str:
        """Cancel reminders — 'cancel all reminders' or by keyword match."""
        msg_lower = user_message.lower()

        if "all" in msg_lower:
            try:
                await self._run_command(["redis-cli", "DEL", "sentient:reminders"], timeout=3)
                return "All reminders cleared."
            except Exception as e:
                return f"Error clearing reminders: {e}"

        # Try to match by keyword
        try:
            result = await self._run_command(
                ["redis-cli", "ZRANGEBYSCORE", "sentient:reminders", "-inf", "+inf"],
                timeout=3
            )
            if not result or result.strip() == "":
                return "No reminders to cancel."

            lines = result.strip().split("\n")
            removed = 0
            for line in lines:
                try:
                    data = json.loads(line.strip().strip('"'))
                    # Check if any word from user message matches reminder text
                    words = set(re.findall(r'\w+', msg_lower)) - {
                        'cancel', 'delete', 'remove', 'clear', 'stop', 'reminder', 'reminders', 'the', 'a', 'my'
                    }
                    reminder_words = set(re.findall(r'\w+', data.get('text', '').lower()))
                    if words & reminder_words:
                        await self._run_command(
                            ["redis-cli", "ZREM", "sentient:reminders", line.strip().strip('"')],
                            timeout=3
                        )
                        removed += 1
                except (json.JSONDecodeError, KeyError):
                    pass

            if removed > 0:
                return f"Cancelled {removed} reminder{'s' if removed > 1 else ''}."
            else:
                return "No matching reminders found. Try 'cancel all reminders' or mention a keyword."
        except Exception as e:
            return f"Error cancelling reminders: {e}"

    # =================================================================
    # SERVICE CONTROL
    # =================================================================

    async def _execute_service_control(self, command: str, user_message: str = "") -> str:
        """Execute service control commands with safety checks."""
        if command == "restart_service":
            msg_lower = user_message.lower()
            match = re.search(
                r'restart\s+(sentient[-_]?\w+|' + '|'.join(self.ALLOWED_SERVICES) + r')',
                msg_lower
            )
            if not match:
                return "Which service should I restart? Available: " + ", ".join(sorted(self.ALLOWED_SERVICES))

            service = match.group(1).replace('_', '-')
            if service.startswith('sentient-'):
                service = service[len('sentient-'):]

            if service not in self.ALLOWED_SERVICES:
                return f"Cannot restart '{service}' - not in allowed list. Available: " + ", ".join(sorted(self.ALLOWED_SERVICES))

            full_name = f"sentient-{service}.service"
            result = await self._run_command(['sudo', 'systemctl', 'restart', full_name], timeout=15)
            await asyncio.sleep(2)
            status = await self._run_command(['systemctl', 'is-active', full_name])
            return f"Restarted {full_name}: {status}"

        return "Unknown service control command"

    # =================================================================
    # COMMAND EXECUTION
    # =================================================================

    async def _run_command(self, cmd: list, timeout: int = 10) -> str:
        """Execute a command safely using asyncio subprocess."""
        try:
            # Special handling for ps command (limit output)
            if cmd[0] == "ps":
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=timeout)

                if process.returncode != 0:
                    return f"Error (exit {process.returncode}): {stderr.decode().strip()}"

                lines = stdout.decode().split('\n')[:11]
                return '\n'.join(lines)

            # Standard command execution
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=timeout)

            if process.returncode != 0:
                logger.warning(f"Command {cmd[0]} failed: {stderr.decode()}")
                return f"Error (exit {process.returncode}): {stderr.decode().strip()}"

            return stdout.decode().strip()

        except asyncio.TimeoutError:
            logger.error(f"Command {cmd[0]} timed out after {timeout}s")
            return f"Error: Command timed out after {timeout} seconds"
        except Exception as e:
            logger.error(f"Command {cmd[0]} failed: {e}")
            return f"Error: {str(e)}"

    # =================================================================
    # OUTPUT FORMATTING
    # =================================================================

    def format_for_prompt(self, tool_name: str, output: str) -> str:
        """Format tool output for LLM prompt injection."""
        max_lines = 20
        lines = output.split('\n')
        if len(lines) > max_lines:
            truncated = '\n'.join(lines[:max_lines])
            output = f"{truncated}\n... (truncated)"

        return f"[Facts you know: {output}] Answer using these facts."

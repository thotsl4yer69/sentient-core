"""Natural-language routing regression: keep the assistant predictable.

These tests verify which subsystem receives a request; they do not assert
successful hardware or cloud execution. Fixtures intentionally vary wording.
"""
import pytest
from sentient.services.conversation.system_tools import SystemTools


CASES = [
    # Clock: no LLM needed.
    ("what time is it", ("clock", "current")),
    ("what's the time", ("clock", "current")),
    ("tell me the time", ("clock", "current")),
    ("time please", ("clock", "current")),
    ("current time", ("clock", "current")),
    ("what time is it now?", ("clock", "current")),
    ("what's the date today", ("clock", "current")),
    ("what is the date", ("clock", "current")),
    ("what day is it", ("clock", "current")),
    ("today's date", ("clock", "current")),
    ("hey jarvis, what time is it", ("clock", "current")),
    ("hey jarvis tell me the time", ("clock", "current")),

    # Discoverable capabilities.
    ("what can you do", ("skills", "catalog")),
    ("list your skills", ("skills", "catalog")),
    ("show your skills", ("skills", "catalog")),
    ("what are your capabilities", ("skills", "catalog")),
    ("hey jarvis, what can you do", ("skills", "catalog")),

    # HA actions: no room, device or content is hardcoded.
    ("turn on study light", ("home_assistant", "handle")),
    ("turn study light on", ("home_assistant", "handle")),
    ("turn off bedroom lamp", ("home_assistant", "handle")),
    ("switch on the desk fan", ("home_assistant", "handle")),
    ("switch the kitchen fan off", ("home_assistant", "handle")),
    ("make study light violet", ("home_assistant", "handle")),
    ("set kitchen lights to red", ("home_assistant", "handle")),
    ("change bedroom light blue", ("home_assistant", "handle")),
    ("turn the office lights purple", ("home_assistant", "handle")),
    ("dim the study light to 20 percent", ("home_assistant", "handle")),
    ("set study light to 50%", ("home_assistant", "handle")),
    ("make it to 30 percent", ("home_assistant", "handle")),
    ("run script sleep music", ("home_assistant", "handle")),
    ("activate scene bedtime", ("home_assistant", "handle")),
    ("start the scene movie night", ("home_assistant", "handle")),
    ("play sleep music on the tv", ("home_assistant", "handle")),
    ("play bedtime music on lounge speaker", ("home_assistant", "handle")),
    ("pause music on tv", ("home_assistant", "handle")),
    ("resume the music on lounge speaker", ("home_assistant", "handle")),
    ("stop playback on tv", ("home_assistant", "handle")),
    ("play the music on living room tv", ("home_assistant", "handle")),
    ("list lights", ("home_assistant", "handle")),
    ("show devices", ("home_assistant", "handle")),
    ("which speakers are available", ("home_assistant", "handle")),
    ("what scenes are there", ("home_assistant", "handle")),
    ("show scripts", ("home_assistant", "handle")),
    ("make study light violet and play sleep music on the tv", ("home_assistant", "handle")),
    ("turn on lounge light and run script music", ("home_assistant", "handle")),
    ("turn bedroom lights violet and run script bedtime", ("home_assistant", "handle")),

    # Actual system skills already in Sentient Core.
    ("how much ram", ("system_info", "memory")),
    ("gpu status", ("system_info", "gpu_status")),
    ("cpu temperature", ("system_info", "temperature")),
    ("disk space", ("system_info", "disk")),
    ("system uptime", ("system_info", "uptime")),
    ("show services", ("service_status", "list_services")),
    ("run diagnostic", ("self_diagnostic", "all")),
    ("health check", ("self_diagnostic", "all")),
    ("what model are you using", ("self_awareness", "my_model")),
    ("what is your name", ("self_awareness", "whoami")),
    ("list installed models", ("ollama", "list_models")),
    ("what is the weather", ("weather", "current_weather")),
    ("remind me in 10 minutes to check the car", ("reminders", "set_reminder")),
    ("list my reminders", ("reminders", "list_reminders")),
    ("cancel my reminder", ("reminders", "cancel_reminder")),
    ("remember that I like jazz", ("memory_tools", "store_memory")),
    ("do you remember when we tested it", ("memory_tools", "search_memory")),
]


@pytest.mark.parametrize("utterance,expected", CASES)
def test_intent_routing(utterance, expected):
    assert SystemTools().detect_intent(utterance) == expected

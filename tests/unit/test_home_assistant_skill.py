"""Home Assistant voice skills never assume device names or claim unexecuted actions."""
import asyncio

from sentient.services.conversation.home_assistant import (
    HomeAssistantSkill, match_entity,
)
from sentient.services.conversation.system_tools import SystemTools


STATES = [
    {"entity_id": "light.bedroom_main", "state": "on",
     "attributes": {"friendly_name": "Bedroom main light"}},
    {"entity_id": "light.bedroom_lamp", "state": "off",
     "attributes": {"friendly_name": "Bedroom lamp"}},
    {"entity_id": "light.study", "state": "on",
     "attributes": {"friendly_name": "Study Light"}},
    {"entity_id": "switch.desk_fan", "state": "off",
     "attributes": {"friendly_name": "Desk fan"}},
    {"entity_id": "media_player.living_room_tv", "state": "idle",
     "attributes": {"friendly_name": "Living Room TV"}},
    {"entity_id": "script.sleep_music", "state": "off",
     "attributes": {"friendly_name": "Sleep Music"}},
]


class FakeHA(HomeAssistantSkill):
    def __init__(self, states=STATES):
        super().__init__("http://homeassistant.test:8123", "test-token")
        self.states = states
        self.calls = []

    async def request(self, path, body=None):
        self.calls.append((path, body))
        if path == "/api/states":
            return self.states, None
        return [], None


def run(value):
    return asyncio.run(value)


def test_dynamic_discovery_and_no_hardcoded_room():
    ha = FakeHA()
    output = run(ha.execute("Show me the lights and speakers"))
    assert "Bedroom main light" in output
    assert "Living Room TV" in output


def test_ambiguous_room_does_not_control_multiple_lights():
    ha = FakeHA()
    output = run(ha.execute("turn bedroom lights violet"))
    assert "Which light" in output
    assert ha.calls == [("/api/states", None)]


def test_exact_target_color_action():
    ha = FakeHA()
    output = run(ha.execute("make study light violet"))
    assert output.startswith("Done:")
    assert ha.calls[-1] == (
        "/api/services/light/turn_on",
        {"entity_id": "light.study", "color_name": "violet"},
    )


def test_pause_actual_media_player():
    ha = FakeHA()
    output = run(ha.execute("pause on living room tv"))
    assert output.startswith("Done:")
    assert ha.calls[-1] == (
        "/api/services/media_player/media_pause",
        {"entity_id": "media_player.living_room_tv"},
    )


def test_run_script_from_actual_entity_list():
    ha = FakeHA()
    output = run(ha.execute("run script sleep music"))
    assert output.startswith("Done:")
    assert ha.calls[-1] == (
        "/api/services/script/turn_on",
        {"entity_id": "script.sleep_music"},
    )


def test_no_fabricated_entity_on_unknown_target():
    ha = FakeHA()
    output = run(ha.execute("turn on garage light"))
    assert "can't find" in output
    assert ha.calls == [("/api/states", None)]


def test_entity_matches_are_specific_and_ambiguous_if_necessary():
    found, choices = match_entity(STATES, "light", "bedroom")
    assert found is None
    assert len(choices) == 2
    found, choices = match_entity(STATES, "light", "study")
    assert found["entity_id"] == "light.study"


def test_unsupported_command_does_not_hit_service_endpoint():
    ha = FakeHA()
    output = run(ha.execute("unlock the front door"))
    assert "can list Home Assistant devices" in output
    assert ha.calls == [("/api/states", None)]


def test_clock_intent_is_deterministic():
    tools = SystemTools()
    assert tools.detect_intent("what time is it") == ("clock", "current")
    assert tools.detect_intent("what's the date today") == ("clock", "current")
    answer = run(tools.execute("clock", "current", "what time is it"))
    assert "on " in answer and ":" in answer


def test_smart_home_intent_routes_without_room_names():
    tools = SystemTools()
    assert tools.detect_intent("make study light violet") == (
        "home_assistant", "handle"
    )
    assert tools.detect_intent("run script sleep music") == (
        "home_assistant", "handle"
    )


def test_compound_command_executes_both_verified_actions():
    ha = FakeHA()
    result = run(ha.execute("make study light violet and play sleep music on the tv", user_id="compound_user"))
    assert result.count("Done:") == 2
    assert ha.calls[1] == (
        "/api/services/light/turn_on",
        {"entity_id": "light.study", "color_name": "violet"},
    )
    assert ha.calls[2] == (
        "/api/services/script/turn_on",
        {"entity_id": "script.sleep_music"},
    )
    assert "not confirmed" in result


def test_compound_command_stops_when_first_step_is_ambiguous():
    ha = FakeHA()
    result = run(ha.execute("turn bedroom lights violet and run script sleep music"))
    assert "Which light" in result
    assert ha.calls == [("/api/states", None)]


def test_brightness_followup_uses_previous_entity_but_only_for_same_user():
    ha = FakeHA()
    run(ha.execute("make study light violet", user_id="brightness_user"))
    next_request = FakeHA()
    result = run(next_request.execute("make it to 30 percent", user_id="brightness_user"))
    assert result.startswith("Done:")
    assert next_request.calls[-1] == (
        "/api/services/light/turn_on",
        {"entity_id": "light.study", "brightness_pct": 30},
    )
    other = FakeHA()
    assert "Which light" in run(other.execute("make it to 30 percent", user_id="other_user"))
    assert other.calls == [("/api/states", None)]


def test_nonspecified_music_does_not_fake_start():
    ha = FakeHA()
    result = run(ha.execute("play nonexistent music on the tv"))
    assert "No playback was started" in result
    assert ha.calls == [("/api/states", None)]


def test_skill_registry_reports_configuration():
    skills = SystemTools()
    assert skills.detect_intent("what can you do") == ("skills", "catalog")
    assert skills.detect_intent("make study light violet and play sleep music on the tv") == (
        "home_assistant", "handle"
    )
    assert skills.detect_intent("make it to 30 percent") == ("home_assistant", "handle")
    summary = run(skills.execute("skills", "catalog"))
    assert "Available skills" in summary and "Alexa" in summary

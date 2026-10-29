"""Dynamic, allowlisted Home Assistant voice skills.

Uses the states endpoint to resolve actual entity names on every command.
Never hardcodes rooms, entity IDs, IP addresses, or credentials.
"""
import asyncio
import json
import os
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


_RECENT_ENTITIES = {}  # (user, entity) -> (domain, entity_id, last_seen_monotonic)

COLORS = (
    "violet", "purple", "blue", "red", "green", "yellow",
    "orange", "pink", "white", "cyan", "magenta", "indigo",
)


def _terms(value):
    tokens = re.findall(r"[a-z0-9]+", str(value).lower().replace("_", " "))
    ignored = {
        "a", "an", "the", "my", "in", "at", "of", "to", "all",
        "light", "lights", "switch", "switches",
        "tv", "television", "speaker", "speakers", "player",
        "scene", "script", "device", "devices", "room",
    }
    return set(tokens) - ignored


def match_entity(states, domain, target):
    """Return (single match, candidates), never guess when ambiguous."""
    available = [item for item in states
                 if str(item.get("entity_id", "")).startswith(domain + ".")]
    query = _terms(target)
    if not query:
        if len(available) == 1:
            return available[0], available
        return None, available

    exact = []
    partial = []
    for item in available:
        entity_id = item["entity_id"]
        name = item.get("attributes", {}).get("friendly_name") or entity_id
        names = [_terms(name), _terms(entity_id.split(".", 1)[1])]
        if any(query == candidate for candidate in names):
            exact.append(item)
        elif any(query.issubset(candidate) for candidate in names):
            partial.append(item)

    choices = exact or partial
    return (choices[0] if len(choices) == 1 else None), choices


class HomeAssistantSkill:
    def __init__(self, base_url=None, token=None):
        self.base_url = (base_url if base_url is not None else
                         os.environ.get("SENTIENT_HOME_ASSISTANT_URL", "")).rstrip("/")
        self.token = token if token is not None else os.environ.get(
            "SENTIENT_HOME_ASSISTANT_TOKEN", "")

    def _request_sync(self, path, body=None):
        if not self.base_url or not self.token:
            return None, "Home Assistant isn't configured on this service."
        if not re.fullmatch(r"https?://[^/?#]+(?::\d+)?", self.base_url):
            return None, "Home Assistant URL is invalid."
        headers = {
            "Authorization": "Bearer " + self.token,
            "Content-Type": "application/json",
        }
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = Request(self.base_url + path, headers=headers, data=data,
                          method="POST" if data is not None else "GET")
        try:
            with urlopen(request, timeout=5) as response:
                payload = response.read(512 * 1024)
                return json.loads(payload), None
        except HTTPError as exc:
            return None, f"Home Assistant request failed (HTTP {exc.code})."
        except (URLError, TimeoutError, ValueError, OSError):
            return None, "Home Assistant is not responding."

    async def request(self, path, body=None):
        return await asyncio.to_thread(self._request_sync, path, body)

    async def _states(self):
        data, error = await self.request("/api/states")
        if error:
            return None, error
        if not isinstance(data, list):
            return None, "Home Assistant returned an invalid device list."
        return data, None

    @staticmethod
    def _describe(item):
        attrs = item.get("attributes") or {}
        return f"{attrs.get('friendly_name') or item['entity_id']} ({item['entity_id']})"

    async def _perform(self, states, domain, target, action, options=None):
        match, choices = match_entity(states, domain, target)
        if not match:
            if choices:
                names = ", ".join(self._describe(x) for x in choices[:6])
                return f"Which {domain.replace('_', ' ')}? I found: {names}."
            return f"I can't find a matching {domain.replace('_', ' ')} in Home Assistant."
        entity = match["entity_id"]
        # Explicit allowlist: never interpolate service action or domain
        # supplied by untrusted speech directly into an endpoint.
        allowed = {
            ("light", "turn_on"), ("light", "turn_off"),
            ("switch", "turn_on"), ("switch", "turn_off"),
            ("media_player", "media_play"), ("media_player", "media_pause"),
            ("media_player", "media_stop"),
            ("scene", "turn_on"), ("script", "turn_on"),
        }
        if (domain, action) not in allowed:
            return "That device action isn't supported."
        payload = {"entity_id": entity}
        payload.update(options or {})
        _, error = await self.request(f"/api/services/{domain}/{action}", payload)
        if error:
            return error
        if getattr(self, "_active_user", None) and domain in {"light", "switch", "media_player"}:
            _RECENT_ENTITIES[self._active_user] = (domain, entity, time.monotonic())
        return f"Done: {action.replace('_', ' ')} — {self._describe(match)}."

    async def execute(self, utterance, user_id="voice_user"):
        self._active_user = user_id
        text = re.sub(r"^\s*(?:hey\s+jarvis[,.]?\s+)", "",
                      utterance.strip(), flags=re.IGNORECASE).lower().strip(" .!?")
        states, error = await self._states()
        if error:
            return error
        # Compound instructions are executed sequentially and reported per step;
        # ambiguity halts before any remaining actions run.
        clauses = re.split(r"\s+and\s+", text, maxsplit=3)
        if len(clauses) > 1:
            outcomes = []
            for clause in clauses:
                outcome = await self._execute_one(states, clause)
                outcomes.append(outcome)
                if outcome.startswith(("Which ", "I can't", "That ", "Home Assistant ", "I can ")):
                    break
            return " ".join(outcomes)
        return await self._execute_one(states, text)

    async def _execute_one(self, states, text):
        # Anaphora is scoped to a user and expires after ten minutes.
        followup = re.fullmatch(r"(?:make|set|dim)\s+it\s+(?:to\s+)?(\d{1,3})\s*(?:percent|%)", text)
        if followup:
            recent = _RECENT_ENTITIES.get(getattr(self, "_active_user", ""), None)
            if not recent or time.monotonic() - recent[2] > 600 or recent[0] != "light":
                return "Which light do you mean?"
            level = int(followup.group(1))
            if not 0 <= level <= 100:
                return "Brightness must be between zero and 100 percent."
            domain, entity, _ = recent
            current = next((x for x in states if x.get("entity_id") == entity), None)
            if current is None:
                return "I can't find that light in Home Assistant anymore."
            return await self._perform(states, domain, entity.split(".", 1)[1],
                                       "turn_on", {"brightness_pct": level})

        if re.search(r"\b(?:list|show|which|what|available)\b", text) and re.search(
            r"\b(?:light|lights|switch|switches|speaker|speakers|tv|devices|entities|scenes|scripts)\b",
            text
        ):
            present = [item for item in states if item.get("entity_id", "").split(".")[0]
                       in {"light", "switch", "media_player", "scene", "script"}]
            if not present:
                return "No controllable lights, switches, media players, scenes, or scripts found."
            return "Home Assistant devices: " + "; ".join(
                self._describe(item) for item in present[:25]
            ) + ("; more devices available" if len(present) > 25 else "")

        color_match = re.fullmatch(
            r"(?:set|make|change|turn)\s+(.+?)\s+(?:to\s+)?(" +
            "|".join(COLORS) + r")", text)
        if color_match:
            return await self._perform(states, "light", color_match.group(1),
                                       "turn_on", {"color_name": color_match.group(2)})

        # Explicit on/off commands with both common word orders.
        match = (re.fullmatch(r"(?:turn|switch)\s+(on|off)\s+(.+)", text)
                 or re.fullmatch(r"(?:turn|switch)\s+(.+?)\s+(on|off)", text))
        if match:
            left, right = match.groups()
            if left in ("on", "off"):
                action, target = left, right
            else:
                target, action = left, right
            # Resolve across both device types; don't assume that every
            # on/off request addresses a light rather than a switch.
            found = []
            candidates = []
            for domain in ("light", "switch"):
                chosen, choices = match_entity(states, domain, target)
                candidates.extend(choices)
                if chosen is not None:
                    found.append((domain, chosen))
            if len(found) == 1 and len(candidates) == 1:
                return await self._perform(
                    states, found[0][0], target, "turn_" + action
                )
            if candidates:
                return "Which device? I found: " + ", ".join(
                    self._describe(item) for item in candidates[:6]
                ) + "."
            return "I can't find that device in Home Assistant."

        match = re.fullmatch(
            r"(?:run|activate|start)\s+(?:the\s+)?(scene|script)\s+(.+)", text
        )
        if match:
            return await self._perform(states, match.group(1),
                                       match.group(2), "turn_on")

        # Named content must be resolved to a *real* HA script or scene;
        # "play X on TV" must not silently resume unrelated media.
        named = re.fullmatch(r"(?:play|start)\s+(.+?)\s+on\s+(?:the\s+)?(.+)", text)
        if named:
            title, destination = named.groups()
            for domain in ("script", "scene"):
                chosen, matches = match_entity(states, domain, title)
                if chosen is not None:
                    result = await self._perform(states, domain, title, "turn_on")
                    if result.startswith("Done:"):
                        return result + (
                            " The selected playback device is controlled by that "
                            "Home Assistant routine, not confirmed by this command."
                        )
                    return result
                if matches:
                    return "Which routine? " + ", ".join(self._describe(x) for x in matches[:5])
            return ("I can't find a Home Assistant script or scene called "
                    + title + " for " + destination + ". No playback was started.")

        brightness = re.fullmatch(
            r"(?:set|dim|brighten)\s+(.+?)\s+(?:to\s+)?(\d{1,3})\s*(?:percent|%)", text
        )
        if brightness:
            target, level = brightness.groups()
            value = int(level)
            if not 0 <= value <= 100:
                return "Brightness must be between zero and 100 percent."
            return await self._perform(states, "light", target, "turn_on",
                                       {"brightness_pct": value})

        match = re.fullmatch(
            r"(pause|resume|stop|play)\s+(?:the\s+)?(?:music|media|playback)?\s*(?:on\s+)?(.*)", text
        )
        if match:
            action = {"pause": "media_pause", "resume": "media_play",
                      "play": "media_play", "stop": "media_stop"}[match.group(1)]
            return await self._perform(states, "media_player", match.group(2), action)

        return ("I can list Home Assistant devices, control lights and switches, "
                "play or pause an exposed media player, and activate named scenes or scripts. "
                "Tell me which device or scene.")

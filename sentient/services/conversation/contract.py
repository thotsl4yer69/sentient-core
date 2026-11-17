"""Load and check contract/mqtt.yaml without a third-party YAML package.

The file is the source of truth for orin/v1 topics. sentient.common.mqtt_topics
keeps a generated copy of the topic strings so other services can import
constants without parsing YAML.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

MELBOURNE = ZoneInfo("Australia/Melbourne")

GENERATED_START = "# BEGIN GENERATED ORIN/V1 - from contract/mqtt.yaml. Do not edit by hand."
GENERATED_END = "# END GENERATED ORIN/V1"

_TS_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$"
)
_TURN_ID_RE = re.compile(r"^[A-Za-z0-9*][A-Za-z0-9_.:-]{0,127}$")

_TOKEN = tuple[int, str, int]


def contract_path() -> Path:
    """Locate contract/mqtt.yaml from this file or SC_CONTRACT_PATH."""
    override = os.environ.get("SC_CONTRACT_PATH")
    if override:
        path = Path(override)
        if not path.is_file():
            raise FileNotFoundError(path)
        return path
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "contract" / "mqtt.yaml"
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("contract/mqtt.yaml")


def load_contract(path: Path | None = None) -> dict[str, Any]:
    """Parse the contract and reject a document that does not match the rules."""
    document = loads((path or contract_path()).read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("contract root must be a mapping")
    _validate_document(document)
    return document


def loads(text: str) -> Any:
    """Parse the restricted YAML subset used by contract/mqtt.yaml."""
    lines = _tokenize(text)
    if not lines:
        return None
    value, index = _parse(lines, 0, lines[0][0])
    if index != len(lines):
        indent, content, lineno = lines[index]
        raise ValueError(f"yaml not fully consumed at line {lineno}: {content} (indent {indent})")
    return value


def topic_by_id(contract: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {topic["id"]: topic for topic in contract["topics"]}


def topic_by_name(contract: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {topic["topic"]: topic for topic in contract["topics"]}


def render_generated_block(contract: dict[str, Any]) -> str:
    """Render the mqtt_topics.py section that must match this contract."""
    lines = [GENERATED_START, f'ORIN_PREFIX = "{contract["prefix"]}"', ""]
    for topic in contract["topics"]:
        lines.append(f'{topic["constant"]} = "{topic["topic"]}"')
    lines.append("")
    lines.append("ORIN_TOPICS = {")
    for topic in contract["topics"]:
        lines.append(f'    "{topic["id"]}": {topic["constant"]},')
    lines.append("}")
    lines.append("")
    lines.append("ORIN_QOS = {")
    for topic in contract["topics"]:
        lines.append(f'    "{topic["id"]}": {int(topic["qos"])},')
    lines.append("}")
    lines.append("")
    lines.append("ORIN_RETAIN = {")
    for topic in contract["topics"]:
        lines.append(f'    "{topic["id"]}": {bool(topic["retain"])},')
    lines.append("}")
    lines.append("")
    lines.append("ORIN_LWT = {")
    for topic in contract["topics"]:
        lines.append(f'    "{topic["id"]}": {bool(topic["lwt"])},')
    lines.append("}")
    lines.append(GENERATED_END)
    return "\n".join(lines) + "\n"


def extract_generated_block(source: str) -> str:
    start = source.find(GENERATED_START)
    end = source.find(GENERATED_END)
    if start < 0 or end < 0 or end < start:
        raise ValueError("mqtt_topics.py is missing the generated orin/v1 block")
    end = end + len(GENERATED_END)
    block = source[start:end]
    if not block.endswith("\n"):
        block += "\n"
    return block


def assert_constants_match(contract: dict[str, Any], module: Any) -> None:
    """Raise if the imported constants drifted from contract/mqtt.yaml."""
    if module.ORIN_PREFIX != contract["prefix"]:
        raise RuntimeError("ORIN_PREFIX does not match contract/mqtt.yaml")
    expected = render_generated_block(contract)
    # Compare the live mappings as well as the rendered text when the source
    # is unavailable (installed copies still expose the dicts).
    for topic in contract["topics"]:
        ident = topic["id"]
        if module.ORIN_TOPICS[ident] != topic["topic"]:
            raise RuntimeError(f"{ident} topic string drifted from the contract")
        if int(module.ORIN_QOS[ident]) != int(topic["qos"]):
            raise RuntimeError(f"{ident} qos drifted from the contract")
        if bool(module.ORIN_RETAIN[ident]) != bool(topic["retain"]):
            raise RuntimeError(f"{ident} retain drifted from the contract")
        if bool(module.ORIN_LWT[ident]) != bool(topic["lwt"]):
            raise RuntimeError(f"{ident} lwt drifted from the contract")
        if getattr(module, topic["constant"]) != topic["topic"]:
            raise RuntimeError(f'{topic["constant"]} drifted from the contract')
    if not expected.startswith(GENERATED_START):
        raise RuntimeError("renderer produced an empty contract block")


def validate_payload(topic_id: str, payload: Any, contract: dict[str, Any]) -> list[str]:
    """Return schema errors for one orin/v1 payload. Empty means valid."""
    spec = topic_by_id(contract).get(topic_id)
    if spec is None:
        return [f"unknown topic {topic_id}"]
    if not isinstance(payload, dict):
        return ["payload must be an object"]
    errors: list[str] = []
    policy = spec["turn_id"]
    if policy == "absent" and "turn_id" in payload:
        errors.append("turn_id is not used on this topic")
    if policy == "required" and not payload.get("turn_id"):
        errors.append("turn_id is required")
    if payload.get("turn_id") == "*" and topic_id != "turn/cancel":
        errors.append("wildcard turn_id is only valid on turn/cancel")
    required = spec["payload"]["required"]
    properties = spec["payload"]["properties"]
    for name in required:
        if name not in payload:
            errors.append(f"missing {name}")
    for name, value in payload.items():
        rule = properties.get(name)
        if rule is None:
            continue
        errors.extend(_check_type(name, value, rule))
    return errors


def melbourne_now(moment: datetime | None = None) -> datetime:
    if moment is None:
        return datetime.now(MELBOURNE)
    if moment.tzinfo is None:
        return moment.replace(tzinfo=MELBOURNE)
    return moment.astimezone(MELBOURNE)


def format_ts(moment: datetime | None = None) -> str:
    """ISO-8601 timestamp in Australia/Melbourne, with AEDT or AEST offset."""
    return melbourne_now(moment).isoformat(timespec="milliseconds")


def local_time_phrase(moment: datetime | None = None) -> str:
    """Prompt fragment that names the civil time and the daylight-saving abbreviation."""
    local = melbourne_now(moment)
    zone = local.tzname() or ""
    return local.strftime("%A %d %B %Y %H:%M ") + zone


def normalize_ts(value: Any, *, now: datetime | None = None) -> str:
    """Accept an ISO string or a unix timestamp and return Melbourne ISO-8601."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return format_ts(datetime.fromtimestamp(float(value), MELBOURNE))
    if isinstance(value, str) and _TS_RE.match(value):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return format_ts(parsed)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            parsed = None
        if parsed is not None:
            return format_ts(parsed)
    return format_ts(now)


def _check_type(name: str, value: Any, rule: dict[str, Any]) -> list[str]:
    kind = rule["type"]
    if kind == "string":
        if not isinstance(value, str):
            return [f"{name} must be a string"]
        if name in ("turn_id",) and not _TURN_ID_RE.match(value):
            return [f"{name} has an invalid form"]
        if name in ("ts",) or rule.get("format") == "date-time":
            if name == "ts" and not _TS_RE.match(value):
                return [f"{name} must be an ISO-8601 timestamp with a timezone"]
        if "enum" in rule and value not in rule["enum"]:
            return [f"{name} must be one of {rule['enum']}"]
        return []
    if kind == "boolean":
        if not isinstance(value, bool):
            return [f"{name} must be a boolean"]
        return []
    if kind == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            return [f"{name} must be an integer"]
        return []
    if kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return [f"{name} must be a number"]
        return []
    return [f"{name} has an unknown schema type {kind}"]


def _validate_document(document: dict[str, Any]) -> None:
    prefix = document.get("prefix")
    if prefix != "orin/v1":
        raise ValueError(f"prefix must be orin/v1, got {prefix!r}")
    guard = document.get("loop_guard") or {}
    if guard.get("field") != "bridge" or guard.get("value") != "sc-compat":
        raise ValueError("loop_guard must be bridge=sc-compat")
    topics = document.get("topics")
    if not isinstance(topics, list) or not topics:
        raise ValueError("topics must be a non-empty list")
    seen_ids: set[str] = set()
    seen_constants: set[str] = set()
    known_topics: set[str] = set()
    for topic in topics:
        ident = topic["id"]
        if ident in seen_ids:
            raise ValueError(f"duplicate topic id {ident}")
        seen_ids.add(ident)
        constant = topic["constant"]
        if constant in seen_constants or not constant.isidentifier():
            raise ValueError(f"bad constant name {constant}")
        seen_constants.add(constant)
        expected = f"{prefix}/{ident}"
        if topic["topic"] != expected:
            raise ValueError(f"{ident} topic must be {expected}")
        known_topics.add(topic["topic"])
        if int(topic["qos"]) not in (0, 1, 2):
            raise ValueError(f"{ident} qos out of range")
        if topic["turn_id"] not in ("required", "optional", "absent"):
            raise ValueError(f"{ident} turn_id policy is invalid")
        required = topic["payload"]["required"]
        if "ts" not in required or "source" not in required:
            raise ValueError(f"{ident} payload must require ts and source")
        if topic["turn_id"] == "required" and "turn_id" not in required:
            raise ValueError(f"{ident} marks turn_id required but omits it from the payload")
        if topic["turn_id"] == "absent" and "turn_id" in required:
            raise ValueError(f"{ident} must not require turn_id")
        if not topic["publishers"] or not topic["subscribers"]:
            raise ValueError(f"{ident} needs a publisher and a subscriber")
    bridges = document.get("bridges")
    if not isinstance(bridges, list) or not bridges:
        raise ValueError("bridges must list the legacy sentient/ mappings")
    persona = False
    for bridge in bridges:
        if bridge["orin"] not in known_topics:
            raise ValueError(f'bridge orin topic is not in the contract: {bridge["orin"]}')
        if bridge["direction"] not in ("both", "to_orin", "to_legacy"):
            raise ValueError(f'bad bridge direction for {bridge["legacy"]}')
        if bridge["legacy"] == "sentient/persona/chat/input" and bridge["direction"] in ("both", "to_orin"):
            persona = True
            if bridge["orin"] != "orin/v1/chat/input":
                raise ValueError("persona chat input must map to orin/v1/chat/input")
    if not persona:
        raise ValueError("contract must bridge sentient/persona/chat/input onto orin/v1/chat/input")


def _tokenize(text: str) -> list[_TOKEN]:
    lines: list[_TOKEN] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        content = _strip_comment(raw.strip())
        if content:
            lines.append((indent, content, lineno))
    return lines


def _strip_comment(text: str) -> str:
    out: list[str] = []
    quote = ""
    for char in text:
        if quote:
            out.append(char)
            if char == quote:
                quote = ""
            continue
        if char in ('"', "'"):
            quote = char
            out.append(char)
            continue
        if char == "#":
            break
        out.append(char)
    return "".join(out).strip()


def _parse(lines: list[_TOKEN], index: int, indent: int) -> tuple[Any, int]:
    if index >= len(lines) or lines[index][0] < indent:
        return None, index
    if lines[index][1].startswith("- "):
        return _parse_list(lines, index, lines[index][0])
    return _parse_map(lines, index, lines[index][0])


def _parse_map(lines: list[_TOKEN], index: int, indent: int) -> tuple[dict[str, Any], int]:
    mapping: dict[str, Any] = {}
    while index < len(lines):
        current, content, lineno = lines[index]
        if current < indent or content.startswith("- "):
            break
        if current != indent:
            raise ValueError(f"unexpected indent at line {lineno}")
        key, separator, rest = content.partition(":")
        if not separator:
            raise ValueError(f"expected key at line {lineno}")
        rest = rest.strip()
        index += 1
        if rest == "":
            if index < len(lines) and lines[index][0] > current:
                child, index = _parse(lines, index, lines[index][0])
                mapping[key.strip()] = child
            else:
                mapping[key.strip()] = None
        else:
            mapping[key.strip()] = _scalar(rest)
    return mapping, index


def _parse_list(lines: list[_TOKEN], index: int, indent: int) -> tuple[list[Any], int]:
    items: list[Any] = []
    while index < len(lines):
        current, content, lineno = lines[index]
        if current < indent:
            break
        if current != indent or not content.startswith("- "):
            break
        rest = content[2:].strip()
        index += 1
        if rest == "":
            if index >= len(lines) or lines[index][0] <= current:
                raise ValueError(f"empty list item at line {lineno}")
            child, index = _parse(lines, index, lines[index][0])
            items.append(child)
            continue
        key, separator, value = rest.partition(":")
        if separator and not rest.startswith(("'", '"')):
            item: dict[str, Any] = {}
            value = value.strip()
            if value == "":
                if index < len(lines) and lines[index][0] > current:
                    child, index = _parse(lines, index, lines[index][0])
                    item[key.strip()] = child
                else:
                    item[key.strip()] = None
            else:
                item[key.strip()] = _scalar(value)
            cont = current + 2
            while index < len(lines) and lines[index][0] == cont and not lines[index][1].startswith("- "):
                ckey, csep, crest = lines[index][1].partition(":")
                if not csep:
                    raise ValueError(f"expected key at line {lines[index][2]}")
                crest = crest.strip()
                index += 1
                if crest == "":
                    if index < len(lines) and lines[index][0] > cont:
                        child, index = _parse(lines, index, lines[index][0])
                        item[ckey.strip()] = child
                    else:
                        item[ckey.strip()] = None
                else:
                    item[ckey.strip()] = _scalar(crest)
            items.append(item)
        else:
            items.append(_scalar(rest))
    return items, index


def _scalar(text: str) -> Any:
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ('"', "'"):
        return text[1:-1]
    if text == "true":
        return True
    if text == "false":
        return False
    if text in ("null", "~"):
        return None
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d+\.\d+", text):
        return float(text)
    return text

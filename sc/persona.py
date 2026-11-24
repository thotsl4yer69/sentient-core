"""Build the prompt for one turn: persona, truthful capabilities, time, memories, history.

The persona text comes first and is identical across turns, so llama-server's
prompt cache (``cache_prompt``) reuses its KV state; only the short context
block after it changes per turn.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sc.memory import Memory, Message, Session

VOICE_RULES = (
    "This is a spoken conversation. Reply in one to three short sentences that sound "
    "natural read aloud. No lists, markdown, emoji, URLs or stage directions. "
    "Say numbers and times the way a person would."
)
CHAT_RULES = "This is a typed chat. Keep replies tight; use short paragraphs or a short list only when it helps."


@dataclass(frozen=True)
class Capabilities:
    voice: bool
    memory: bool = True

    def describe(self) -> str:
        can = ["hold a conversation and keep context within it"]
        if self.memory:
            can.append("remember what people tell you across conversations and recall it later")
        if self.voice:
            can.append("listen and speak through the speakerphone")
        cannot = [
            "see cameras or sensors (none are connected to you right now)",
            "run commands, change settings or control devices",
            "browse the internet",
        ]
        return (
            "What you can do right now: " + "; ".join(can) + ".\n"
            "What you cannot do right now: " + "; ".join(cannot) + "."
        )


def load_persona(path: Path) -> str:
    text = Path(path).read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"persona file {path} is empty")
    return text


def _when(ts: float, tz: ZoneInfo, now: float) -> str:
    dt = datetime.fromtimestamp(ts, tz)
    days = (now - ts) / 86400.0
    if days < 1 and datetime.fromtimestamp(now, tz).date() == dt.date():
        return "earlier today"
    if days < 2:
        return "yesterday"
    if days < 14:
        return f"{int(days)} days ago"
    return dt.strftime("%-d %b %Y")


def build_messages(
    *,
    persona: str,
    assistant_name: str,
    capabilities: Capabilities,
    session: Session,
    speaker: str,
    kind: str,
    memories: list[Memory],
    history: list[dict[str, str]],
    user_text: str,
    timezone: str,
    now: float | None = None,
) -> list[dict[str, str]]:
    now = time.time() if now is None else now
    tz = ZoneInfo(timezone)
    local = datetime.fromtimestamp(now, tz)
    present = ", ".join(p.capitalize() for p in session.participants)
    lines = [
        persona,
        "",
        "## Right now",
        capabilities.describe(),
        f"Local time: {local.strftime('%A %-d %B %Y, %-I:%M %p')} ({local.tzname()}).",
        f"People in this conversation: {present}. The latest message is from {speaker.capitalize()}.",
        VOICE_RULES if kind == "voice" else CHAT_RULES,
    ]
    if memories:
        lines.append("")
        lines.append("## Things you remember (what people told you; may be out of date)")
        for m in sorted(memories, key=lambda m: m.ts):
            lines.append(f"- {_when(m.ts, tz, now)}, {m.speaker.capitalize()} said: \"{m.text}\"")
        lines.append("Use these only if they're relevant. Don't recite them unprompted.")
    messages = [{"role": "system", "content": "\n".join(lines)}]
    for item in history:
        role = item["role"]
        if role not in ("user", "assistant"):
            continue
        content = item["content"]
        messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_text})
    return messages


def history_from_log(log: list[Message], session: Session) -> list[dict[str, str]]:
    """Server-side history; in multi-person sessions user lines carry the speaker."""
    multi = len(session.participants) > 1
    out = []
    for m in log:
        content = f"{m.speaker.capitalize()}: {m.content}" if (multi and m.role == "user") else m.content
        out.append({"role": m.role, "content": content})
    return out

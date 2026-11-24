"""Turn a streamed reply into speakable sentences."""

from __future__ import annotations

import re

_END = re.compile(r"([.!?…]+[\"')\]]*)(\s+|$)")
_MD = re.compile(r"(\*\*|__|`+|^#+\s*|^\s*[-*•]\s+|^\s*\d+[.)]\s+)", re.MULTILINE)
_URL = re.compile(r"https?://\S+")
_EMOJI = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF]")
_BRACKETS = re.compile(r"\[[^\]]{1,60}\]")
_STAR = re.compile(r"\*([^*\s][^*]{0,58})\*")
_ABBREV = {"e.g.", "i.e.", "etc.", "mr.", "mrs.", "ms.", "dr.", "st.", "vs."}


def _star(m: re.Match) -> str:
    """``*...*`` is a stage direction when it stands apart from the sentence
    ("*smiles* Sure.", "Hi! *waves*", "Got it *nods*") and emphasis when it is
    part of one ("called *Biscuit*.", "*so* cool"). Emphasis keeps its words:
    dropping it once lost the answer itself."""
    text, inner = m.string, m.group(1)
    before = text[: m.start()].rstrip()
    after = text[m.end():]
    if after[:1] in (".", "!", "?", ",", ";", ":", "…"):
        return inner                        # punctuation attached: part of the sentence
    leading = not before or before[-1] in ".!?…"
    trailing = not after.strip()
    if leading:
        return ""                           # opens the sentence or stands alone: an action
    if trailing:
        return inner if inner[:1].isupper() else ""   # "is *Biscuit*" vs "got it *nods*"
    return inner                            # mid-sentence emphasis


def speakable(text: str) -> str:
    text = _URL.sub("the link", text)
    # Bold/code markers first: otherwise "**Sure** ... *smiles*" pairs the
    # wrong asterisks as one stage direction.
    text = _MD.sub("", text)
    text = _EMOJI.sub("", text)
    text = _BRACKETS.sub("", text)
    text = _STAR.sub(_star, text)
    return " ".join(text.split())


def plain(text: str) -> str:
    """Every word of the reply with the markup removed, for a reply that
    ``speakable`` would reduce to nothing ("*wags tail*")."""
    text = _URL.sub("the link", text)
    text = _MD.sub("", text)
    text = _EMOJI.sub("", text)
    return " ".join(text.replace("*", " ").replace("[", " ").replace("]", " ").split())


class SentenceSplitter:
    """Feed deltas, get complete sentences as soon as they end.

    The first chunk is released early (at a comma after ~6 words) so speech can
    start before the model finishes a long first sentence.
    """

    def __init__(self, first_chunk_words: int = 6):
        self._buf = ""
        self._first = True
        self._first_words = first_chunk_words

    def feed(self, delta: str) -> list[str]:
        self._buf += delta
        out: list[str] = []
        while True:
            m = _END.search(self._buf)
            cut = None
            while m:
                candidate = self._buf[: m.end(1)]
                last_word = candidate.split()[-1].lower() if candidate.split() else ""
                if last_word not in _ABBREV and not re.search(r"\d\.$", candidate[-3:]):
                    cut = m
                    break
                m = _END.search(self._buf, m.end())
            if cut is None:
                if self._first and len(self._buf.split()) >= self._first_words and "," in self._buf:
                    head, _, tail = self._buf.rpartition(",")
                    if len(head.split()) >= self._first_words:
                        out.append(head + ",")
                        self._buf = tail
                        self._first = False
                break
            sentence = self._buf[: cut.end(1)]
            self._buf = self._buf[cut.end():]
            if sentence.strip():
                out.append(sentence)
                self._first = False
        return [s for s in (speakable(s) for s in out) if s]

    def flush(self) -> list[str]:
        rest, self._buf = self._buf, ""
        s = speakable(rest)
        return [s] if s else []

"""Map Piper/espeak phonemes onto the 15 Oculus visemes TalkingHead drives."""

from __future__ import annotations

OCULUS_VISEMES = (
    "sil",
    "PP",
    "FF",
    "TH",
    "DD",
    "kk",
    "CH",
    "SS",
    "nn",
    "RR",
    "aa",
    "E",
    "I",
    "O",
    "U",
)

_SILENCE = {"", " ", "^", "$", "_", "sil", "sp", "pau", "rest", "h#", "ˌ", "ˈ"}

# Keys are normalised (lower case, length marks and stress digits removed).
_MAP = {
    "p": "PP",
    "b": "PP",
    "m": "PP",
    "f": "FF",
    "v": "FF",
    "θ": "TH",
    "ð": "TH",
    "th": "TH",
    "dh": "TH",
    "t": "DD",
    "d": "DD",
    "ɾ": "DD",
    "k": "kk",
    "g": "kk",
    "ɡ": "kk",
    "ŋ": "nn",
    "ng": "nn",
    "n": "nn",
    "l": "nn",
    "s": "SS",
    "z": "SS",
    "ʃ": "CH",
    "ʒ": "CH",
    "tʃ": "CH",
    "dʒ": "CH",
    "ch": "CH",
    "jh": "CH",
    "sh": "CH",
    "zh": "CH",
    "r": "RR",
    "ɹ": "RR",
    "ɚ": "RR",
    "ɝ": "RR",
    "j": "I",
    "y": "I",
    "w": "U",
    "h": "sil",
    "a": "aa",
    "ɑ": "aa",
    "æ": "aa",
    "ʌ": "aa",
    "ɐ": "aa",
    "ɒ": "aa",
    "ə": "aa",
    "aɪ": "aa",
    "aʊ": "aa",
    "e": "E",
    "ɛ": "E",
    "eɪ": "E",
    "ɛɪ": "E",
    "eə": "E",
    "ɛə": "E",
    "i": "I",
    "ɪ": "I",
    "ɪə": "I",
    "o": "O",
    "ɔ": "O",
    "oʊ": "O",
    "əʊ": "O",
    "ɔɪ": "O",
    "u": "U",
    "ʊ": "U",
    "ʊə": "U",
}


def _normalise(phoneme: str) -> str:
    text = str(phoneme).strip().lower()
    text = text.replace("ː", "").replace(":", "")
    text = text.replace("ˈ", "").replace("ˌ", "")
    return "".join(ch for ch in text if not ch.isdigit())


def map_phoneme(phoneme: str) -> tuple[str, bool]:
    """Return (viseme, known). Unknown phonemes become sil and known is False."""
    key = _normalise(phoneme)
    if key in _SILENCE:
        return "sil", True
    viseme = _MAP.get(key)
    if viseme is None:
        return "sil", False
    return viseme, True

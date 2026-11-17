"""Turn Piper phoneme/sample alignments into a TalkingHead viseme timeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from sentient.services.avatar_stream.visemes import map_phoneme


@dataclass(frozen=True)
class VisemeCue:
    viseme: str
    start_ms: int
    duration_ms: int

    def as_dict(self) -> dict[str, int | str]:
        return {"viseme": self.viseme, "start_ms": self.start_ms, "duration_ms": self.duration_ms}


def _sample_count(item: Mapping[str, object] | object) -> tuple[str, int]:
    if isinstance(item, Mapping):
        phoneme = item.get("phoneme", "")
        samples = item.get("num_samples")
    else:
        phoneme = getattr(item, "phoneme", "")
        samples = getattr(item, "num_samples", None)
    if not isinstance(samples, (int, float)) or isinstance(samples, bool):
        raise ValueError("alignment num_samples must be a number")
    count = int(samples)
    if count < 0:
        raise ValueError("alignment num_samples cannot be negative")
    return str(phoneme), count


def _ms(samples: int, sample_rate: int) -> int:
    return int(round(samples * 1000.0 / sample_rate))


def alignments_to_cues(alignments: Iterable[object], sample_rate: int) -> list[VisemeCue]:
    """Map phoneme alignments to merged viseme cues on a millisecond clock.

    ``sample_rate`` is the PCM rate of the chunk. Times are cumulative from the
    start of that chunk. Adjacent identical visemes collapse into one cue.
    BOS, EOS and PAD stay in the timeline as silence so the mouth is closed
    on the edges of the utterance.
    """
    if sample_rate <= 0:
        raise ValueError("sample_rate must be positive")
    rows: Sequence[object] = tuple(alignments)
    cues: list[VisemeCue] = []
    cursor = 0
    for item in rows:
        phoneme, samples = _sample_count(item)
        start = _ms(cursor, sample_rate)
        cursor += samples
        end = _ms(cursor, sample_rate)
        if samples == 0 or end <= start:
            continue
        viseme, _known = map_phoneme(phoneme)
        duration = end - start
        if cues and cues[-1].viseme == viseme and cues[-1].start_ms + cues[-1].duration_ms == start:
            prev = cues[-1]
            cues[-1] = VisemeCue(viseme, prev.start_ms, prev.duration_ms + duration)
        else:
            cues.append(VisemeCue(viseme, start, duration))
    return cues

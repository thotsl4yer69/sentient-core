"""Package one turn's PCM and viseme timeline for the display node."""

from __future__ import annotations

from dataclasses import dataclass

from sentient.services.avatar_stream.timeline import VisemeCue, alignments_to_cues


@dataclass(frozen=True)
class TurnPackage:
    turn_id: str
    sample_rate: int
    pcm_s16le: bytes
    visemes: tuple[str, ...]
    vtimes: tuple[int, ...]
    vdurations: tuple[int, ...]

    @property
    def duration_ms(self) -> int:
        if not self.vtimes:
            return 0
        return self.vtimes[-1] + self.vdurations[-1]

    def header(self) -> dict[str, object]:
        return {
            "type": "turn",
            "turn_id": self.turn_id,
            "sample_rate": self.sample_rate,
            "channels": 1,
            "pcm_format": "s16le",
            "visemes": list(self.visemes),
            "vtimes": list(self.vtimes),
            "vdurations": list(self.vdurations),
            "pcm_bytes": len(self.pcm_s16le),
        }


def package_turn(
    turn_id: str,
    pcm_s16le: bytes,
    sample_rate: int,
    alignments: list[object] | tuple[object, ...],
) -> TurnPackage:
    """Build the per-turn payload. PCM is mono signed 16-bit little endian."""
    if not isinstance(turn_id, str) or not turn_id.strip():
        raise ValueError("turn_id is required")
    if sample_rate < 8000 or sample_rate > 96000:
        raise ValueError("sample_rate out of range")
    if not isinstance(pcm_s16le, (bytes, bytearray)):
        raise ValueError("pcm must be bytes")
    pcm = bytes(pcm_s16le)
    if len(pcm) % 2:
        raise ValueError("pcm length must be even")
    cues = alignments_to_cues(alignments, sample_rate)
    if not cues and pcm:
        samples = len(pcm) // 2
        span = max(1, int(round(samples * 1000.0 / sample_rate)))
        cues = [VisemeCue("sil", 0, span)]
    return TurnPackage(
        turn_id=turn_id.strip(),
        sample_rate=sample_rate,
        pcm_s16le=pcm,
        visemes=tuple(cue.viseme for cue in cues),
        vtimes=tuple(cue.start_ms for cue in cues),
        vdurations=tuple(cue.duration_ms for cue in cues),
    )

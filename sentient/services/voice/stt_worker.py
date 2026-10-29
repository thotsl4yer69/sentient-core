"""MQTT Whisper transcription adapter, with an explicit local model.

No model download occurs at runtime. Works with a locally installed
faster-whisper model and the voice pipeline's WAV/PCM16 contract.
"""
import asyncio
import base64
import binascii
import io
import os
import time
import wave
from pathlib import Path

from sentient.common import mqtt_topics
from sentient.common.service_base import SentientService
from sentient.common.voice_timing import voice_stage


def decode_voice_wav(encoded: str):
    """Validate the wire format and return normalized 16-kHz mono samples."""
    import numpy as np
    try:
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("Audio exceeds two-megabyte limit")
        with wave.open(io.BytesIO(raw), "rb") as src:
            if (src.getframerate(), src.getnchannels(), src.getsampwidth()) != (16000, 1, 2):
                raise ValueError("Audio must be mono 16kHz, 16-bit PCM WAV")
            if src.getcomptype() != "NONE":
                raise ValueError("Compressed WAV not accepted")
            audio = src.readframes(src.getnframes())
        return np.frombuffer(audio, dtype="<i2").astype(np.float32) / 32768.0
    except (wave.Error, EOFError, binascii.Error) as exc:
        raise ValueError("Invalid base64 or WAV audio") from exc


class WhisperSTTWorker(SentientService):
    def __init__(self):
        super().__init__(name="whisper_stt")
        self.model = None
        self._transcribe_lock = asyncio.Lock()
        self.model_path = os.getenv("SENTIENT_STT_MODEL", "").strip()
        self.device = os.getenv("SENTIENT_STT_DEVICE", "cpu")
        self.compute_type = os.getenv("SENTIENT_STT_COMPUTE_TYPE", "int8")

        @self.on_mqtt(mqtt_topics.STT_AUDIO_INPUT)
        async def incoming(topic, payload):
            import json
            data = json.loads(payload.decode("utf-8"))
            await self.handle_audio(data)

    async def setup(self):
        if not self.model_path or not Path(self.model_path).is_dir():
            raise RuntimeError(
                "SENTIENT_STT_MODEL must point to an installed local "
                "faster-whisper / CTranslate2 model directory"
            )
        from faster_whisper import WhisperModel
        self.model = await asyncio.to_thread(
            WhisperModel, self.model_path,
            device=self.device,
            compute_type=self.compute_type,
            local_files_only=True,
        )
        self.logger.info("Local STT model loaded (%s)", self.device)

    def _transcribe(self, samples):
        segments, info = self.model.transcribe(
            samples, language="en", beam_size=1,
            condition_on_previous_text=False, vad_filter=False,
        )
        text = " ".join(segment.text.strip() for segment in segments).strip()
        return text, getattr(info, "language_probability", 0.0)

    async def handle_audio(self, request):
        turn_id = request.get("turn_id")
        started = time.monotonic()
        voice_stage(self.logger, turn_id, "stt_received")
        result = {
            "text": "", "language": "en", "language_probability": 0.0,
            "source": "whisper_stt", "turn_id": turn_id,
        }
        async with self._transcribe_lock:
            voice_stage(self.logger, turn_id, "stt_job_started")
            try:
                audio = request["audio"]
                if audio.get("format") != "wav":
                    raise ValueError("Expected WAV format")
                samples = decode_voice_wav(audio["data"])
                voice_stage(self.logger, turn_id, "stt_decode_complete")
                voice_stage(self.logger, turn_id, "stt_inference_started")
                result["text"], result["language_probability"] = (
                    await asyncio.to_thread(self._transcribe, samples)
                )
                voice_stage(self.logger, turn_id, "stt_inference_complete")
            except Exception as exc:
                result["error"] = str(exc)
                self.logger.error("STT failed for %s: %s", turn_id, exc)
            result["duration_ms"] = round((time.monotonic() - started) * 1000)
            voice_stage(self.logger, turn_id, "stt_publishing",
                        duration_ms=result["duration_ms"])
            await self.mqtt_publish(mqtt_topics.STT_OUTPUT, result)


if __name__ == "__main__":
    asyncio.run(WhisperSTTWorker().run())

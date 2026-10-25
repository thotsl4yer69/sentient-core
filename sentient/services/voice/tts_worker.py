"""Piper + local playback adapter for Sentient Core's MQTT voice protocol.

The active Nano must have piper, a voice model, and a working audio player.
No cloud API, speaker ID or model is silently selected or downloaded.
"""
import asyncio
import os
import shutil
import tempfile
import uuid
from pathlib import Path

from sentient.common import mqtt_topics
from sentient.common.service_base import SentientService


class PiperTTSWorker(SentientService):
    def __init__(self):
        super().__init__(name="piper_tts")
        self.piper_bin = os.getenv("SENTIENT_PIPER_BIN", "piper")
        self.player_bin = os.getenv("SENTIENT_AUDIO_PLAYER", "aplay")
        self.voice_model = os.getenv("SENTIENT_PIPER_MODEL", "")
        self._play_lock = asyncio.Lock()
        self._active_process = None
        self._cancel_requested = False

        @self.on_mqtt(mqtt_topics.TTS_SPEAK)
        async def speak(topic, payload):
            import json
            await self.speak(json.loads(payload.decode("utf-8")))

        @self.on_mqtt(mqtt_topics.TTS_STOP)
        async def stop(topic, payload):
            await self.stop()

    async def setup(self):
        if not self.voice_model or not Path(self.voice_model).is_file():
            raise RuntimeError("SENTIENT_PIPER_MODEL must be an existing .onnx file")
        if not (shutil.which(self.piper_bin) or Path(self.piper_bin).is_file()):
            raise RuntimeError("Piper executable not found")
        if not (shutil.which(self.player_bin) or Path(self.player_bin).is_file()):
            raise RuntimeError("Audio player executable not found")
        self.logger.info("Local Piper speech ready")

    async def stop(self):
        self._cancel_requested = True
        proc = self._active_process
        if proc is not None and proc.returncode is None:
            proc.terminate()

    async def _run_speech_process(self, cmd, stdin=None):
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdin=asyncio.subprocess.PIPE if stdin is not None else None,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        self._active_process = proc
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(stdin), timeout=90)
            if proc.returncode != 0:
                if self._cancel_requested:
                    raise asyncio.CancelledError()
                raise RuntimeError(
                    f"Speech process exited {proc.returncode}: "
                    + stderr.decode("utf-8", "replace")[:400]
                )
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
            if self._active_process is proc:
                self._active_process = None

    async def speak(self, request):
        text = str(request.get("text") or "").strip()
        if not text:
            return
        turn_id = request.get("turn_id")
        tts_id = uuid.uuid4().hex
        async with self._play_lock:
            self._cancel_requested = False
            status = "completed"
            with tempfile.TemporaryDirectory(prefix="airi-tts-") as folder:
                wav_path = os.path.join(folder, "speech.wav")
                try:
                    await self._run_speech_process(
                        (self.piper_bin, "--model", self.voice_model,
                         "--output_file", wav_path),
                        stdin=text.encode("utf-8"),
                    )
                    if self._cancel_requested:
                        status = "cancelled"
                    else:
                        await self.mqtt_publish(mqtt_topics.TTS_STARTED, {
                            "id": tts_id, "turn_id": turn_id, "source": "piper_tts",
                        })
                        await self._run_speech_process(
                            (self.player_bin, wav_path)
                        )
                        if self._cancel_requested:
                            status = "cancelled"
                except asyncio.CancelledError:
                    status = "cancelled"
                except Exception as exc:
                    status = "error"
                    self.logger.error("TTS failure for %s: %s", turn_id, exc)
                finally:
                    await self.mqtt_publish(mqtt_topics.TTS_COMPLETED, {
                        "id": tts_id, "turn_id": turn_id,
                        "status": status, "source": "piper_tts",
                    })


if __name__ == "__main__":
    asyncio.run(PiperTTSWorker().run())

"""Interruptible CPU Piper TTS for the Sentient Core voice/avatar MQTT pipeline.

Requires an installed Piper CLI and a real .onnx voice model specified in
SENTIENT_PIPER_MODEL. Runs on the CPU; no additional GPU model is loaded.
"""
import asyncio
import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path

from sentient.common import mqtt_topics
from sentient.common.service_base import SentientService


class PiperTTS(SentientService):
    def __init__(self):
        super().__init__(name="piper_tts")
        self._play_task = None
        self._process = None
        self._request_id = None
        self._piper = os.environ.get("SENTIENT_PIPER_BIN", "piper")
        self._player = os.environ.get("SENTIENT_AUDIO_PLAYER", "aplay")
        self._model = os.environ.get("SENTIENT_PIPER_MODEL", "")

        @self.on_mqtt(mqtt_topics.TTS_SPEAK)
        async def on_speak(topic, payload):
            try:
                message = json.loads(payload.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self.logger.warning("Ignoring malformed TTS request")
                return
            text = message.get("text", "")
            if not isinstance(text, str) or not text.strip():
                return
            request_id = str(message.get("id") or uuid.uuid4().hex)
            await self._cancel_current()
            self._request_id = request_id
            self._play_task = asyncio.create_task(self._speak(text[:1200], request_id))

        @self.on_mqtt(mqtt_topics.TTS_STOP)
        async def on_stop(topic, payload):
            await self._cancel_current()

    async def setup(self):
        if not self._model or not Path(self._model).is_file():
            raise RuntimeError("Set SENTIENT_PIPER_MODEL to an installed .onnx voice file")
        for executable in (self._piper, self._player):
            if not shutil.which(executable):
                raise RuntimeError(f"Required CPU audio executable not installed: {executable}")
        self.logger.info("Piper CPU TTS ready with voice: %s", self._model)

    async def _cancel_current(self):
        task = self._play_task
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        self._play_task = None

    async def _run_process(self, args, input_bytes=None):
        process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE if input_bytes is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        self._process = process
        try:
            _, err = await process.communicate(input=input_bytes)
            if process.returncode != 0:
                raise RuntimeError(f"{args[0]} exited {process.returncode}: {err.decode(errors='replace')[-300:]}")
        except asyncio.CancelledError:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 2)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
            raise
        finally:
            if self._process is process:
                self._process = None

    async def _speak(self, text, request_id):
        status = "error"
        try:
            with tempfile.TemporaryDirectory(prefix="sentient-piper-") as directory:
                wav = str(Path(directory) / "speech.wav")
                await self._run_process(
                    [self._piper, "--model", self._model, "--output_file", wav],
                    input_bytes=text.encode("utf-8"),
                )
                if not Path(wav).is_file() or Path(wav).stat().st_size < 44:
                    raise RuntimeError("Piper did not produce a valid WAV")
                await self.mqtt_publish(mqtt_topics.TTS_STARTED, {
                    "id": request_id, "status": "speaking"
                })
                player_args = [self._player]
                if Path(self._player).name == "aplay":
                    player_args.append("-q")
                await self._run_process(player_args + [wav])
            status = "completed"
        except asyncio.CancelledError:
            status = "interrupted"
            raise
        except Exception as exc:
            self.logger.error("Piper TTS failed for %s: %s", request_id, exc)
        finally:
            await self.mqtt_publish(mqtt_topics.TTS_COMPLETED, {
                "id": request_id, "status": status
            })

    async def teardown(self):
        await self._cancel_current()


if __name__ == "__main__":
    asyncio.run(PiperTTS().run())

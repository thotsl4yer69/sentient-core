# Jetson Orin Nano 8GB — local model upgrade (October 2026)

**Status:** Code prepared; this is *not* evidence the live Orin has been updated.
Only deploy from the actual Orin after inspecting free RAM, current services,
audio hardware and available JetPack/L4T version.

## Model selection

- **Default:** Google Gemma 4 E2B, Q4 QAT GGUF, llama.cpp. Smaller GPU footprint
  and text/image/audio input capabilities (this deployment enables the **text**
  endpoint only). NVIDIA Jetson AI Lab recommends E2B for Orin Nano 8GB.
- **Alternative:** NVIDIA Nemotron 3.5 Bolt E4B, Q4_K_M GGUF, roughly 4GB
  of model weights, suited to conversational and tool-use workloads. More
  memory overhead at runtime; use a shorter context and test it separately.
- **Existing:** Ollama remains installed, configured and available for
  per-request fallback. Do not run multiple large models loaded concurrently.
- **Voice:** CPU Piper is provided in `sentient.services.voice.tts` for
  installations with an actual voice ONNX file and working audio device. It
  publishes existing MQTT started/completed topics and handles interruption.
  No synthetic/placeholder voice data or automatic model download.

## Preflight / setup on the Orin

Run in a **current checkout of this branch**, not on a remote laptop:

```bash
cd /opt/sentient-core
sudo bash scripts/jetson-models.sh check
```

Only L4T R36.x with a functioning NVIDIA Docker runtime is accepted by the
launcher; this doesn't flash JetPack or modify system drivers. Check disk
usage; by default models cache in `/mnt/orin-ai/hf-cache`, with a >=7GB
free-space gate. Override `SENTIENT_HF_CACHE` if necessary.

Stage the 8GB default:

```bash
sudo bash scripts/jetson-models.sh start gemma
sudo bash scripts/jetson-models.sh logs
sudo bash scripts/jetson-models.sh test
```

The launcher uses NVIDIA's Orin-specific `llama.cpp` container, exposes the
model **only** on `127.0.0.1:18080`, and leaves existing Ollama untouched.
The initial container/model download needs internet access. It refuses to
start while Ollama has an actively loaded model because 8GB memory is shared.

Once the local server passes **real text generation**, opt in:

```bash
sudo bash scripts/jetson-models.sh activate
sudo bash scripts/jetson-models.sh status
```

Activation writes a tracked systemd drop-in for any installed contemplation
service: `SENTIENT_LLM_BACKEND=openai`,
`SENTIENT_LLM_BASE_URL=http://127.0.0.1:18080`. The old Ollama backend remains
as a fallback on empty or failed generation. Streaming failover only retries
before the first token to avoid corrupting output.

**Rollback:**

```bash
sudo bash scripts/jetson-models.sh rollback
sudo bash scripts/jetson-models.sh stop
```

Rollback restores the prior drop-in (if one existed) and restarts the
contemplation service. It doesn't delete downloaded weights.

To benchmark **Nemotron** instead, roll back and stop the managed container,
then run `sudo bash scripts/jetson-models.sh start nemotron`, `test`, and
`activate`. Switch back to Gemma the same way. Keep concurrency at 1.

## CPU Piper voice integration

The old systemd unit referenced a missing `services/piper_tts.py`. The
new unit invokes `python3 -m sentient.services.voice.tts`. This requires
the existing project's Python dependencies (including `aiomqtt` and the
voice service dependencies), an actual installed Piper CLI, an ALSA player,
and an installed ONNX voice file. Do not enable a second TTS service if the
existing voice pipeline already publishes playback events.

Example **only with a verified installed voice file**:

```bash
export SENTIENT_PIPER_MODEL="$(find /opt/sentient-core /mnt/orin-ai -maxdepth 5 -type f -name '*.onnx' -print -quit)"
test -f "$SENTIENT_PIPER_MODEL" && command -v piper && command -v aplay
```

Place `SENTIENT_PIPER_MODEL` in the private systemd environment file referenced
by the unit. Confirm MQTT permissions and audio playback, then enable the
unit as appropriate. Existing STT, wake-word and avatar processes are not
changed by the model staging script.

## Verification

```bash
python3 -m unittest discover -s tests/unit -p test_local_openai.py -v
bash -n scripts/jetson-models.sh
curl -sf http://127.0.0.1:18080/v1/models
```

Measure voice round-trip, first token, sustained memory, temperatures, model
correctness/tool invocation and wake-word behaviour on the *actual* Orin.
Neither the GPU throughput nor the voice latency of this deployment is known
until these tests run there.

## References

- [Jetson AI Lab: Gemma 4 on Jetson](https://www.jetson-ai-lab.com/tutorials/gemma4-on-jetson/)
- [Jetson AI Lab: Nemotron 3.5 Bolt E4B 4GB](https://www.jetson-ai-lab.com/models/Nemotron-3-5-Bolt-E4B-4GB/)

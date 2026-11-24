# Sentient Core v3 on nanob — runbook

Four services on the one Jetson Orin Nano 8 GB, nothing in the cloud:

| unit | what | listens |
|---|---|---|
| `sc-llm` | llama.cpp `llama-server`, Gemma 4 E2B QAT q4_0, CUDA (pinned Jetson container, local model file) | 127.0.0.1:18080 |
| `sc-core` | turns, persona, memory (SQLite), OpenAI-compatible API for AIRI and sc-voice | 127.0.0.1:8770 (LAN only with TLS) |
| `sc-stt` | whisper.cpp `whisper-server`, base.en, CUDA | 127.0.0.1:18081 |
| `sc-voice` | USB mic/speaker, wake word, VAD, Piper TTS, barge-in | — |

The old `sentient-*` units, Redis, mosquitto and Ollama are **not touched** by the
installer. They are stopped (not disabled) for the trial and retired one at a
time only after v3 passes acceptance.

Run every step on nanob from a checkout of this branch. Each step's check must
pass before the next.

## 0. Identity (A1)

```sh
python3 tools/sc_acceptance.py identity
```
Record the SSH host-key fingerprints in `campaign/LOG.md`. Every later session
must show the same fingerprints (192.168.1.168 has also been the Windows ATHENA box).

## 1. Backup first (A2)

```sh
sudo python3 tools/orin_backup.py backup --dest /mnt/orin-ai/backups --profile nanob \
  --age-recipient age1... --dry-run          # then without --dry-run
sudo python3 tools/orin_backup.py verify --archive /mnt/orin-ai/backups/orin-backup-*.tar.age \
  --scratch /mnt/orin-ai/backups/restore-test --identity /offline/nanob-backup-identity.txt
```
See `docs/BACKUP.md`. Don't continue unless `verify` passes.

## 2. Free the GPU, the port and the microphone (reversible)

```sh
systemctl list-units 'sentient-*' 'orin-*' 'cortana-*' --no-pager    # note what's running
ollama ps                                                            # loaded models hold shared RAM
sudo bash scripts/jetson-models.sh stop 2>/dev/null || true          # old container on :18080
sudo systemctl stop sentient-core.target orin-voice.service 2>/dev/null || true
```
`stop` only, so a reboot brings the old stack back until step 8. The old voice
services hold the USB microphone; sc-voice can't open it while they run.

## 3. Install

```sh
sudo apt install -y alsa-utils git cmake build-essential iproute2
export HF_TOKEN=...          # only if the Gemma repo asks for it (gated)
sudo deploy/v3/install.sh --users jack,partner --voice --dry-run
sudo deploy/v3/install.sh --users jack,partner --voice
```
Tokens: `/root/sc-tokens.txt` (root only). Give each person only their own line.
Re-running the installer is safe; it keeps `/etc/sc/*` and the memory database.

## 4. Start and check

```sh
sudo systemctl start sc-llm sc-core            # trial run, not enabled at boot yet
curl -s http://127.0.0.1:8770/healthz          # "status":"ok" once the model has loaded
sudo -u sc /opt/sc/venv/bin/python -m sc.voice --list-devices
sudo systemctl start sc-stt sc-voice
journalctl -fu sc-voice                         # "sc-voice ready", then "capture on plughw:CARD=..."
```

## 5. Bring the old memories across

```sh
sudo -u sc env SC_CONFIG=/etc/sc/sc.toml /opt/sc/venv/bin/python -m sc.migrate --owner jack --dry-run
sudo -u sc env SC_CONFIG=/etc/sc/sc.toml /opt/sc/venv/bin/python -m sc.migrate --owner jack
```
Read-only on Redis. Old memories become Jack's private memories.

## 6. Acceptance (campaign/TARGET.md)

```sh
python3 tools/sc_acceptance.py text                       # B1: 10 turns, context, latency
python3 tools/sc_acceptance.py memory --seed              # D1/D2 part 1
sudo reboot
python3 tools/sc_acceptance.py memory --verify            # D1 ≥18/20 after reboot, D2 0/5 leaks
# C1: say "Hey Jarvis" + a question, 10 times, waiting for each reply. Then:
python3 tools/sc_acceptance.py voice --since "15 min ago"
python3 tools/sc_acceptance.py exposure --allow 22        # G1 (add 8770 if LAN+TLS)
python3 tools/sc_acceptance.py resources --minutes 60     # F1
SC_TEST_LLM_URL=http://127.0.0.1:18080 SC_TEST_STRICT=1 \
  /opt/sc/venv/bin/python -m pytest tests/sc/test_real_model.py -s   # B1 strict, from the checkout
```
E1: `sudo systemctl kill sc-core` (then sc-llm, sc-stt, sc-voice), three times
each; `text` must pass again within 15 s. E2: unplug and replug the USB audio
three times; a spoken turn must work within 10 s of each replug
(`journalctl -u sc-voice` shows "capture ... ended" then "capture on ..."). E3: ten
power-cycles, `text` passing within 120 s each time (enable at boot first:
`sudo systemctl enable sc.target sc-llm sc-core sc-stt sc-voice`).
Every result is appended to `sc-acceptance.jsonl`; paste it into `campaign/LOG.md`.

## 7. AIRI

AIRI → Settings → Providers → Chat → **OpenAI Compatible**:

* Base URL: `http://127.0.0.1:8770/v1` when AIRI runs on nanob, otherwise
  `https://<nanob-ip>:8770/v1` after enabling TLS:
  ```sh
  sudo deploy/v3/make-cert.sh <nanob-ip> nanob
  # /etc/sc/sc.toml: bind_host = "0.0.0.0", tls_cert/tls_key = /etc/sc/tls/server.{crt,key}
  sudo systemctl restart sc-core
  ```
  and install `/etc/sc/tls/ca.crt` as trusted on the AIRI device.
* API key: that person's token from `/root/sc-tokens.txt`.
* Model: `cortana`. Then Settings → Modules → Consciousness → select it.

The AIRI web page served over https can only call an https endpoint; the AIRI
desktop app can use either. Leave AIRI's own speech modules off: speech runs
on nanob's speakerphone.

## 8. Make it permanent / roll back

Permanent, after acceptance passes:
```sh
sudo systemctl enable sc.target sc-llm sc-core sc-stt sc-voice
# retire old units one at a time (each reversible):
sudo python3 tools/orin_reconcile.py prepare --unit sentient-web-chat.service --airi-url ...
```
Roll back at any point:
```sh
sudo systemctl disable --now sc-voice sc-stt sc-core sc-llm sc.target
sudo systemctl start sentient-core.target orin-voice.service
```
`/var/lib/sc` (memory), `/etc/sc` (config, tokens) and `/opt/sc` stay in place.

#!/usr/bin/env bash
# Sentient Core v3 installer for the Jetson Orin Nano (nanob).
#
#   sudo deploy/v3/install.sh --dry-run                 # show what would happen
#   sudo deploy/v3/install.sh --users jack,partner      # install, don't start
#   sudo deploy/v3/install.sh --users jack,partner --enable
#   sudo deploy/v3/install.sh --users jack --voice      # also speech: STT, TTS, wake word
#   sudo deploy/v3/install.sh --wheelhouse /path/wheelhouse ...   # offline
#
# Idempotent. Never stops, disables or edits any sentient-* or orin-* unit,
# Ollama, mosquitto or Redis. Existing /etc/sc files are kept, not overwritten.
# Take a backup first (tools/orin_backup.py) — see deploy/v3/README.md.
set -Eeuo pipefail

REPO=$(CDPATH='' cd -- "$(dirname "$0")/../.." && pwd)
PREFIX=/opt/sc
ETC=/etc/sc
DATA=/var/lib/sc
UNIT_DIR=/etc/systemd/system
IMAGE_DEFAULT="ghcr.io/nvidia-ai-iot/llama_cpp:b8708-r36.4-tegra-aarch64-cu126-22.04"
MODEL_REPO_DEFAULT="google/gemma-4-E2B-it-qat-q4_0-gguf"
MODEL_FILE_DEFAULT="gemma-4-E2B_q4_0-it.gguf"
MODEL_SEARCH=(/mnt/orin-ai/hf-cache /root/.cache/huggingface /opt/sc/models)

DRY=0; ENABLE=0; USERS=""; WHEELHOUSE=""; ALLOW_NON_JETSON=0; SKIP_MODEL=0; SKIP_IMAGE=0; VOICE=0
# whisper.cpp pinned to the commit tested with this release
WHISPER_REPO="https://github.com/ggml-org/whisper.cpp"
WHISPER_COMMIT="96ec4d9823e9d0211944172da35f9f29134af990"
OWW_RELEASE="https://github.com/dscripka/openWakeWord/releases/download/v0.5.1"
OWW_MIRROR="https://huggingface.co/harvestsu/openwakeword-onnx/resolve/main"
HF="https://huggingface.co"
# name|sha256|url|fallback-url — every voice model is checked before use
VOICE_MODELS=(
  "whisper/ggml-base.en.bin|a03779c86df3323075f5e796cb2ce5029f00ec8869eee3fdfb897afe36c6d002|$HF/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin|"
  "silero_vad.onnx|a4a068cd6cf1ea8355b84327595838ca748ec29a25bc91fc82e6c299ccdc5808|$HF/onnx-community/silero-vad/resolve/main/onnx/model.onnx|"
  "piper/en_GB-alba-medium.onnx|1f9da809b6da1ef4190bb5dd6cce4f4af803274539e9cf9e0693085150bac3d7|$HF/rhasspy/piper-voices/resolve/main/en/en_GB/alba/medium/en_GB-alba-medium.onnx|"
  "piper/en_GB-alba-medium.onnx.json|ed4b1f015a8aae5eb26d4bdeed7b712c660f5698a0dd954aa6df9c8be60b900b|$HF/rhasspy/piper-voices/resolve/main/en/en_GB/alba/medium/en_GB-alba-medium.onnx.json|"
  "openwakeword/melspectrogram.onnx|ba2b0e0f8b7b875369a2c89cb13360ff53bac436f2895cced9f479fa65eb176f|$OWW_RELEASE/melspectrogram.onnx|$OWW_MIRROR/melspectrogram.onnx"
  "openwakeword/embedding_model.onnx|70d164290c1d095d1d4ee149bc5e00543250a7316b59f31d056cff7bd3075c1f|$OWW_RELEASE/embedding_model.onnx|$OWW_MIRROR/embedding_model.onnx"
  "openwakeword/hey_jarvis_v0.1.onnx|94a13cfe60075b132f6a472e7e462e8123ee70861bc3fb58434a73712ee0d2cb|$OWW_RELEASE/hey_jarvis_v0.1.onnx|$OWW_MIRROR/hey_jarvis_v0.1.onnx"
)
IMAGE="$IMAGE_DEFAULT"; MODEL_REPO="$MODEL_REPO_DEFAULT"; MODEL_FILE="$MODEL_FILE_DEFAULT"

usage() { sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; cat <<'EOF'
Options:
  --users a,b        people who get chat tokens (lowercase); required on first install
  --enable           enable and start sc.target after installing
  --dry-run          print the plan, change nothing
  --wheelhouse DIR   install Python packages offline from DIR (deploy/build-wheels.sh)
  --image REF        llama.cpp container (default: pinned L4T R36.4 / CUDA 12.6 build)
  --model-repo R --model-file F   GGUF to use (default Gemma 4 E2B QAT q4_0)
  --voice            also install speech: whisper.cpp (built for CUDA), Piper, wake word
  --skip-model --skip-image       don't fetch the model / container
  --allow-non-jetson              test on a non-Jetson host (no CUDA container)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY=1 ;;
    --enable) ENABLE=1 ;;
    --voice) VOICE=1 ;;
    --users) USERS=${2:?}; shift ;;
    --wheelhouse) WHEELHOUSE=${2:?}; shift ;;
    --image) IMAGE=${2:?}; shift ;;
    --model-repo) MODEL_REPO=${2:?}; shift ;;
    --model-file) MODEL_FILE=${2:?}; shift ;;
    --skip-model) SKIP_MODEL=1 ;;
    --skip-image) SKIP_IMAGE=1 ;;
    --allow-non-jetson) ALLOW_NON_JETSON=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

say()  { printf '\033[1m==>\033[0m %s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
run()  { if (( DRY )); then printf '    [dry-run] %s\n' "$*"; else "$@"; fi; }

# fetch_pinned DEST SHA256 URL [FALLBACK_URL]: download only if missing or wrong,
# and never keep a file whose sha256 doesn't match.
fetch_pinned() {
  local dest=$1 sha=$2 url=$3 alt=${4:-}
  if [[ -f "$dest" ]] && echo "$sha  $dest" | sha256sum -c --quiet - 2>/dev/null; then return 0; fi
  if (( DRY )); then printf '    [dry-run] fetch %s (sha256 %s…)\n' "$dest" "${sha:0:12}"; return 0; fi
  install -d -m 0755 "$(dirname "$dest")"
  local src
  for src in "$url" $alt; do
    [[ -n "$src" ]] || continue
    if curl -fsSL --retry 3 --max-time 900 -o "$dest.partial" "$src" \
       && echo "$sha  $dest.partial" | sha256sum -c --quiet - 2>/dev/null; then
      mv "$dest.partial" "$dest"; chmod 0644 "$dest"; return 0
    fi
    warn "fetch from $src failed or sha256 mismatch"
  done
  rm -f "$dest.partial"
  die "could not fetch $(basename "$dest") with the pinned sha256"
}

# ── preflight (read-only) ───────────────────────────────────────────────
(( EUID == 0 )) || die "run with sudo"
[[ -f "$REPO/sc/core.py" ]] || die "run from a sentient-core checkout ($REPO has no sc/)"
if [[ -f /etc/nv_tegra_release ]]; then
  say "Jetson: $(head -1 /etc/nv_tegra_release | cut -c1-80)"
elif (( ! ALLOW_NON_JETSON )); then
  die "not a Jetson (/etc/nv_tegra_release missing). Use --allow-non-jetson to test elsewhere."
fi
PY=$(command -v python3.10 || command -v python3) || die "python3 not found"
"$PY" -c 'import sys; assert sys.version_info >= (3, 10)' || die "need Python 3.10+"
"$PY" -c 'import venv' 2>/dev/null || die "python3-venv missing: apt install python3-venv"
say "Python: $("$PY" -V)"

free_kb=$(df -Pk "$(dirname "$PREFIX")" | awk 'NR==2{print $4}')
(( free_kb > 6000000 )) || die "need 6 GB free under $(dirname "$PREFIX") (have $((free_kb/1024)) MB)"

port_owner() {
  # Prints the process listening on TCP port $1, "unknown" if something listens
  # but its owner can't be read, or nothing. Never fails (set -e safe).
  if command -v ss >/dev/null; then
    local line
    line=$(ss -Htlnp "sport = :$1" 2>/dev/null || true)
    [[ -z "$line" ]] && return 0
    sed -n 's/.*users:((\("[^"]*"\).*/\1/p' <<<"$line" | head -1 | grep . || echo unknown
  elif (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; then
    echo unknown
  fi
}
command -v ss >/dev/null || warn "ss not found (iproute2); port ownership checks are coarse"
for port in 8770 18080; do
  owner=$(port_owner "$port")
  if [[ -n "$owner" ]]; then
    if [[ "$port" == 18080 ]] && docker ps --format '{{.Names}}' 2>/dev/null | grep -qx sc-llm; then
      continue
    fi
    if [[ "$port" == 8770 ]] && systemctl is-active -q sc-core.service 2>/dev/null; then
      continue
    fi
    (( ENABLE )) && die "port $port is already used by $owner. Stop that deliberately first (e.g. the legacy sentient-jetson-llm container), then re-run."
    warn "port $port is in use by $owner; sc will not be able to start until it is free"
  fi
done

HAVE_DOCKER=0
if command -v docker >/dev/null && docker info >/dev/null 2>&1; then
  HAVE_DOCKER=1
  docker info --format '{{json .Runtimes}}' | grep -qi nvidia || { (( ALLOW_NON_JETSON )) || die "Docker has no nvidia runtime"; }
elif (( ! ALLOW_NON_JETSON )); then
  die "Docker is required for sc-llm"
fi

if [[ ! -f "$ETC/tokens" && -z "$USERS" ]]; then
  die "first install needs --users (e.g. --users jack,partner)"
fi
IFS=',' read -r -a PEOPLE <<<"$USERS"
for u in "${PEOPLE[@]}"; do
  [[ "$u" =~ ^[a-z][a-z0-9_-]{0,31}$ ]] || die "bad user name '$u' (lowercase letters, digits, _ or -)"
  [[ "$u" != voice ]] || die "'voice' is reserved for the sc-voice service account"
done

say "Plan: $PREFIX (code, venv, models), $ETC (config, tokens), $DATA (memory db)"
(( DRY )) && say "Dry run: nothing below is executed"

# ── user and directories ────────────────────────────────────────────────
if ! id sc >/dev/null 2>&1; then
  run useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin --user-group sc
fi
run install -d -m 0755 -o root -g root "$PREFIX" "$PREFIX/models" "$PREFIX/personality"
run install -d -m 0700 -o sc -g sc "$DATA"
run install -d -m 0750 -o root -g sc "$ETC"

# ── Python environment ──────────────────────────────────────────────────
STAMP="$PREFIX/venv/.sc-source"
SRC_ID=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || echo unknown)
if [[ ! -x "$PREFIX/venv/bin/python" ]]; then
  run "$PY" -m venv "$PREFIX/venv"
fi
if [[ -n "$WHEELHOUSE" ]]; then
  [[ -f "$WHEELHOUSE/constraints.lock" ]] || die "$WHEELHOUSE/constraints.lock missing"
  run "$PREFIX/venv/bin/pip" install --no-index --find-links "$WHEELHOUSE" --require-hashes -r "$WHEELHOUSE/constraints.lock"
  run "$PREFIX/venv/bin/pip" install --no-index --no-deps --no-build-isolation "$REPO"
else
  run "$PREFIX/venv/bin/pip" install --upgrade pip
  if [[ "$(uname -m)" == aarch64 ]] && "$PY" -c 'import sys; sys.exit(sys.version_info[:2] != (3, 10))'; then
    run "$PREFIX/venv/bin/pip" install --require-hashes -r "$REPO/constraints.lock"
    run "$PREFIX/venv/bin/pip" install --no-deps "$REPO"
  else
    warn "not CPython 3.10/aarch64: installing unpinned (test hosts only)"
    run "$PREFIX/venv/bin/pip" install "$REPO"
  fi
fi
(( DRY )) || echo "$SRC_ID" > "$STAMP"
run "$PREFIX/venv/bin/python" -c "import sc.core, sc.api, sc.memory"

run install -m 0644 "$REPO/personality/cortana.md" "$PREFIX/personality/cortana.md"
run install -m 0644 "$REPO/personality/cortana_core.txt" "$PREFIX/personality/cortana_core.txt"

# ── models ──────────────────────────────────────────────────────────────
run "$PREFIX/venv/bin/python" -m sc.embed --fetch --dest "$PREFIX/models/all-MiniLM-L6-v2"

if (( ! SKIP_MODEL )); then
  if [[ -f "$PREFIX/models/$MODEL_FILE" ]]; then
    say "model present: $MODEL_FILE"
  else
    found=""
    for dir in "${MODEL_SEARCH[@]}"; do
      [[ -d "$dir" ]] || continue
      found=$(find -L "$dir" -name "$MODEL_FILE" -type f -size +500M 2>/dev/null | head -1)
      [[ -n "$found" ]] && break
    done
    if [[ -n "$found" ]]; then
      say "reusing existing model file $found"
      run cp --reflink=auto "$found" "$PREFIX/models/$MODEL_FILE.partial"
    else
      say "downloading $MODEL_REPO/$MODEL_FILE (set HF_TOKEN if the repo is gated)"
      auth=()
      [[ -n "${HF_TOKEN:-}" ]] && auth=(-H "Authorization: Bearer $HF_TOKEN")
      run curl -fL --retry 3 "${auth[@]}" -o "$PREFIX/models/$MODEL_FILE.partial" \
        "https://huggingface.co/$MODEL_REPO/resolve/main/$MODEL_FILE"
    fi
    if (( ! DRY )); then
      head -c 4 "$PREFIX/models/$MODEL_FILE.partial" | grep -q GGUF || die "$MODEL_FILE is not a GGUF file"
      mv "$PREFIX/models/$MODEL_FILE.partial" "$PREFIX/models/$MODEL_FILE"
    fi
  fi
  if (( ! DRY )); then
    sums="$PREFIX/models/SHA256SUMS"
    if grep -q " $MODEL_FILE\$" "$sums" 2>/dev/null; then
      (cd "$PREFIX/models" && grep " $MODEL_FILE\$" SHA256SUMS | sha256sum -c --quiet) \
        || die "$MODEL_FILE no longer matches its recorded sha256 (corrupt or replaced)"
    else
      (cd "$PREFIX/models" && sha256sum "$MODEL_FILE" >> SHA256SUMS)
      say "recorded sha256 of $MODEL_FILE"
    fi
    chmod 0644 "$PREFIX/models/$MODEL_FILE"
  fi
fi

if (( ! SKIP_IMAGE && HAVE_DOCKER )); then
  if docker image inspect "$IMAGE" >/dev/null 2>&1; then
    say "container image present: $IMAGE"
  else
    run docker pull "$IMAGE"
  fi
fi

# ── configuration (kept if present) ─────────────────────────────────────
if [[ ! -f "$ETC/sc.toml" ]]; then
  run install -m 0640 -o root -g sc "$REPO/deploy/v3/sc.toml.example" "$ETC/sc.toml"
  (( DRY )) || sed -i "s|^llm_model = .*|llm_model = \"$MODEL_FILE\"|" "$ETC/sc.toml"
fi
if [[ ! -f "$ETC/llm.env" ]]; then
  if (( DRY )); then
    run write "$ETC/llm.env"
  else
    umask 027
    cat > "$ETC/llm.env" <<EOF
SC_LLM_IMAGE=$IMAGE
SC_LLM_MODEL_FILE=$MODEL_FILE
SC_LLM_CTX=4096
SC_LLM_MEMORY=3300m
EOF
    chown root:sc "$ETC/llm.env"
  fi
fi
if [[ ! -f "$ETC/tokens" ]]; then
  if (( DRY )); then
    run write "$ETC/tokens" "for ${PEOPLE[*]} + voice"
  else
    umask 077
    out=/root/sc-tokens.txt
    {
      echo "# Sentient Core tokens — created $(date -Is). Give each person only their own line."
      for u in "${PEOPLE[@]}"; do echo "$u chat $("$PY" -c 'import secrets;print(secrets.token_urlsafe(32))')"; done
      echo "voice chat+speaker $("$PY" -c 'import secrets;print(secrets.token_urlsafe(32))')"
    } > "$out"
    grep -v '^#' "$out" > "$ETC/tokens"
    chown root:sc "$ETC/tokens"; chmod 0640 "$ETC/tokens"; chmod 0600 "$out"
    say "tokens written to $ETC/tokens; your copy is $out (root only)"
  fi
fi

# ── voice (optional) ────────────────────────────────────────────────────
if (( VOICE )); then
  for tool in arecord aplay git cmake g++; do
    command -v "$tool" >/dev/null || die "$tool missing: apt install alsa-utils git cmake build-essential"
  done
  CUDA_FLAGS=(-DGGML_CUDA=OFF)
  if [[ -f /etc/nv_tegra_release ]]; then
    NVCC=/usr/local/cuda/bin/nvcc
    [[ -x "$NVCC" ]] || die "CUDA toolkit missing ($NVCC): it ships with JetPack (nvidia-jetpack / cuda-toolkit)"
    # Orin = Ampere sm_87
    CUDA_FLAGS=(-DGGML_CUDA=ON -DCMAKE_CUDA_COMPILER="$NVCC" -DCMAKE_CUDA_ARCHITECTURES=87)
  fi
  # openWakeWord runs on ONNX here; --no-deps skips its tflite-runtime dependency.
  run "$PREFIX/venv/bin/pip" install "piper-tts==1.8.0" requests tqdm scipy scikit-learn
  run "$PREFIX/venv/bin/pip" install --no-deps "openwakeword==0.6.0"
  run install -d -m 0755 "$PREFIX/bin"
  if [[ "$(cat "$PREFIX/bin/.whisper-commit" 2>/dev/null)" != "$WHISPER_COMMIT" || ! -x "$PREFIX/bin/whisper-server" ]]; then
    say "building whisper-server ${WHISPER_COMMIT:0:7} (${CUDA_FLAGS[0]})"
    if (( ! DRY )); then
      wtmp=$(mktemp -d)
      git -C "$wtmp" init -q && git -C "$wtmp" remote add origin "$WHISPER_REPO"
      git -C "$wtmp" fetch -q --depth 1 origin "$WHISPER_COMMIT"
      git -C "$wtmp" checkout -q FETCH_HEAD
      cmake -S "$wtmp" -B "$wtmp/build" -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF \
        -DWHISPER_BUILD_TESTS=OFF -DWHISPER_BUILD_EXAMPLES=ON "${CUDA_FLAGS[@]}" >/dev/null
      cmake --build "$wtmp/build" --target whisper-server -j "$(nproc)" >/dev/null
      install -m 0755 "$wtmp/build/bin/whisper-server" "$PREFIX/bin/whisper-server"
      echo "$WHISPER_COMMIT" > "$PREFIX/bin/.whisper-commit"
      rm -rf "$wtmp"
    fi
  else
    say "whisper-server ${WHISPER_COMMIT:0:7} already built"
  fi
  for entry in "${VOICE_MODELS[@]}"; do
    IFS='|' read -r name sha url alt <<<"$entry"
    fetch_pinned "$PREFIX/models/$name" "$sha" "$url" "$alt"
  done
  if [[ ! -f "$ETC/voice.toml" ]]; then
    run install -m 0640 -o root -g sc "$REPO/deploy/v3/voice.toml.example" "$ETC/voice.toml"
    if (( ! DRY )) && (( ${#PEOPLE[@]} )); then
      # The speakerphone can't tell voices apart, so by default it is a shared
      # room: everyone in the household counts as present. Voice then only
      # recalls what was said with all of them there, never private chats.
      everyone=$(printf '"%s", ' "${PEOPLE[@]}"); everyone="[${everyone%, }]"
      sed -i "s|^speaker = .*|speaker = \"${PEOPLE[0]}\"|; s|^participants = .*|participants = ${everyone}|" "$ETC/voice.toml"
    fi
  fi
  if [[ ! -f "$ETC/voice.token" ]] && (( ! DRY )); then
    tok=$(awk '$1=="voice"{print $3}' "$ETC/tokens")
    [[ -n "$tok" ]] || die "no 'voice' line in $ETC/tokens; add: voice chat+speaker <token>"
    (umask 027; printf '%s\n' "$tok" > "$ETC/voice.token")
    chown root:sc "$ETC/voice.token"; chmod 0640 "$ETC/voice.token"
  fi
  run "$PREFIX/venv/bin/python" -c "import piper, openwakeword.model, sc.voice.loop"
fi

# ── systemd ─────────────────────────────────────────────────────────────
UNITS=(sc.target sc-llm.service sc-core.service)
(( VOICE )) && UNITS+=(sc-stt.service sc-voice.service)
for unit in "${UNITS[@]}"; do
  run install -m 0644 "$REPO/deploy/v3/units/$unit" "$UNIT_DIR/$unit"
done
run systemctl daemon-reload
if command -v systemd-analyze >/dev/null && (( ! DRY )); then
  (cd "$UNIT_DIR" && systemd-analyze verify "${UNITS[@]}" 2>&1) \
    | grep -v -e 'docker.service' -e 'Unknown key name' || true
fi

if (( ENABLE )); then
  run systemctl enable --now "${UNITS[@]}"
  if (( ! DRY )); then
    say "waiting for sc-core /healthz (model load can take a minute)"
    for _ in $(seq 1 90); do
      code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8770/healthz || true)
      [[ "$code" == 200 ]] && break
      sleep 2
    done
    curl -s http://127.0.0.1:8770/healthz; echo
    [[ "$code" == 200 ]] || warn "not healthy yet: journalctl -u sc-llm -u sc-core -n 50"
  fi
else
  say "installed. Start with: sudo systemctl enable --now ${UNITS[*]}"
fi
say "done (source $SRC_ID)"

#!/usr/bin/env bash
# Build the pinned CPython 3.10 aarch64 wheelhouse on the Pi 500+.
#
# Runs inside an Ubuntu 22.04 arm64 container. Downloads the hashed wheels in
# constraints.lock, compiles PyAudio from its pinned sdist (PyPI publishes no
# cp310 aarch64 wheel), and writes wheelhouse/constraints.lock whose hashes
# match those files. This script never creates a virtualenv on the host and
# never copies one out of the container. A throwaway venv inside the container
# proves the offline install, then is deleted before the container exits.
#
# On the target:
#   python3 -m venv /opt/sc/releases/<ver>/venv
#   /opt/sc/releases/<ver>/venv/bin/pip install --upgrade pip
#   /opt/sc/releases/<ver>/venv/bin/pip install \
#       --no-index --find-links <wheelhouse> --require-hashes \
#       -r <wheelhouse>/constraints.lock
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WHEELHOUSE="${1:-${ROOT}/wheelhouse}"
IMAGE="${WHEEL_IMAGE:-ubuntu:22.04}"
PLATFORM="${WHEEL_PLATFORM:-linux/arm64}"
LOCK="${ROOT}/constraints.lock"
PYAUDIO_VERSION="0.2.14"
PYAUDIO_SDIST_SHA256="78dfff3879b4994d1f4fc6485646a57755c6ee3c19647a491f790a0895bd2f87"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required so the wheelhouse can be built in ${IMAGE} (${PLATFORM})" >&2
  exit 1
fi
if [[ ! -f "${LOCK}" ]]; then
  echo "missing lock file: ${LOCK}" >&2
  exit 1
fi

mkdir -p "${WHEELHOUSE}"
find "${WHEELHOUSE}" -maxdepth 1 -type f \
  \( -name '*.whl' -o -name '*.tar.gz' -o -name '*.zip' -o -name 'constraints.lock' \) \
  -delete

docker run --rm \
  --platform "${PLATFORM}" \
  -e PYAUDIO_VERSION="${PYAUDIO_VERSION}" \
  -e PYAUDIO_SDIST_SHA256="${PYAUDIO_SDIST_SHA256}" \
  -v "${LOCK}:/src/constraints.lock:ro" \
  -v "${WHEELHOUSE}:/wheelhouse" \
  "${IMAGE}" \
  bash -euo pipefail -c "$(cat <<'EOS'
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  python3 \
  python3-pip \
  python3-venv \
  python3-dev \
  gcc \
  pkg-config \
  ca-certificates \
  portaudio19-dev \
  libportaudio2 \
  libgomp1 \
  libglib2.0-0
python3 -m pip install --upgrade pip
python3 -m pip download \
  --require-hashes \
  --only-binary=:all: \
  --dest /wheelhouse \
  -r /src/constraints.lock

mkdir -p /tmp/pyaudio-src /tmp/pyaudio-wheels
python3 -m pip download \
  --no-deps \
  --no-binary=PyAudio \
  --dest /tmp/pyaudio-src \
  "PyAudio==${PYAUDIO_VERSION}"
sdist="/tmp/pyaudio-src/PyAudio-${PYAUDIO_VERSION}.tar.gz"
echo "${PYAUDIO_SDIST_SHA256}  ${sdist}" | sha256sum -c -
python3 -m pip wheel \
  --no-deps \
  --no-index \
  --find-links /tmp/pyaudio-src \
  --wheel-dir /tmp/pyaudio-wheels \
  "PyAudio==${PYAUDIO_VERSION}"
built=(/tmp/pyaudio-wheels/*.whl)
if [[ ${#built[@]} -ne 1 ]]; then
  echo "expected exactly one PyAudio wheel, found ${#built[@]}" >&2
  exit 1
fi
cp "${built[0]}" /wheelhouse/
wheel_hash="$(sha256sum "${built[0]}" | awk '{print $1}')"
cp /src/constraints.lock /wheelhouse/constraints.lock
cat >> /wheelhouse/constraints.lock <<EOF

# Compiled in this ubuntu:22.04 container ($(uname -m)) from PyAudio==${PYAUDIO_VERSION}
# sdist sha256 ${PYAUDIO_SDIST_SHA256}. PyPI has no cp310 aarch64 wheel.
PyAudio==${PYAUDIO_VERSION} \\
    --hash=sha256:${wheel_hash}
EOF

python3 -m venv /tmp/sc-venv
/tmp/sc-venv/bin/pip install --upgrade pip
/tmp/sc-venv/bin/pip install \
  --no-index \
  --find-links /wheelhouse \
  --require-hashes \
  -r /wheelhouse/constraints.lock
/tmp/sc-venv/bin/python - <<'PY'
import importlib
modules = [
    "fastapi",
    "uvicorn",
    "websockets",
    "pydantic",
    "starlette",
    "aiomqtt",
    "paho.mqtt.client",
    "redis",
    "numpy",
    "aiohttp",
    "tomli",
    "colorama",
    "onnxruntime",
    "cv2",
    "webrtcvad",
    "pyaudio",
    "openwakeword",
]
for name in modules:
    importlib.import_module(name)
    print("import ok", name)
import importlib.util
for banned in ("torch", "sentence_transformers"):
    if importlib.util.find_spec(banned) is not None:
        raise SystemExit(f"{banned} must not be installed")
print("offline install ok")
PY
rm -rf /tmp/sc-venv
echo "wheelhouse files: $(find /wheelhouse -maxdepth 1 -type f | wc -l)"
EOS
)"

echo "Wheelhouse: ${WHEELHOUSE}"
echo "Create the venv on the target. Do not copy this container or any venv."
echo "  python3 -m venv /opt/sc/releases/<ver>/venv"
echo "  /opt/sc/releases/<ver>/venv/bin/pip install --upgrade pip"
echo "  /opt/sc/releases/<ver>/venv/bin/pip install --no-index --find-links ${WHEELHOUSE} --require-hashes -r ${WHEELHOUSE}/constraints.lock"

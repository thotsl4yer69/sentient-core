#!/usr/bin/env bash
# Jetson Orin Nano 8GB model deployment: stage -> test -> activate -> rollback.
# No existing Ollama service or model is replaced. Only the dedicated
# sentient-jetson-llm container and service overrides created here are managed.
set -Eeuo pipefail

NAME="sentient-jetson-llm"
IMAGE="ghcr.io/nvidia-ai-iot/llama_cpp:latest-jetson-orin"
CACHE="${SENTIENT_HF_CACHE:-/mnt/orin-ai/hf-cache}"
PORT=18080
URL="http://127.0.0.1:${PORT}"
STATE="/var/lib/sentient-jetson-models"
SERVICES=(sentient-contemplation-http.service sentient-contemplation.service)

die() { echo "ERROR: $*" >&2; exit 1; }
require_root() { [[ "${EUID}" -eq 0 ]] || die "Run with sudo: sudo bash $0 $*"; }
require_docker() {
  command -v docker >/dev/null || die "Docker is not installed"
  docker info >/dev/null 2>&1 || die "Docker daemon unavailable"
}
check_platform() {
  [[ -r /etc/nv_tegra_release ]] || die "Not an NVIDIA Jetson L4T host"
  grep -q 'R36' /etc/nv_tegra_release || die "Expected tested Orin L4T R36.x / JetPack 6; check compatibility before installing"
  docker info --format '{{json .Runtimes}}' | grep -qi nvidia || die "Docker NVIDIA runtime missing"
}
probe() {
  python3 - "${URL}" <<'PY'
import json,sys,urllib.request
with urllib.request.urlopen(sys.argv[1] + "/v1/models", timeout=8) as r:
    data = json.load(r)
assert data.get("data") and data["data"][0].get("id"), "model not ready"
print("READY:", data["data"][0]["id"])
PY
}
test_generation() {
  python3 - "${URL}" <<'PY'
import json,sys,time,urllib.request
url=sys.argv[1]
with urllib.request.urlopen(url+"/v1/models", timeout=8) as r:
    model=json.load(r)["data"][0]["id"]
payload=json.dumps({"model":model,"messages":[{"role":"user","content":"Respond with the single word READY"}],"max_tokens":16,"temperature":0}).encode()
request=urllib.request.Request(url+"/v1/chat/completions",payload,{"Content-Type":"application/json"})
t=time.monotonic()
with urllib.request.urlopen(request,timeout=90) as r:
    data=json.load(r)
answer=data["choices"][0]["message"]["content"]
assert isinstance(answer,str) and answer.strip(), "model produced empty output"
print("MODEL RESPONSE:",answer.strip())
print("ELAPSED SECONDS:",round(time.monotonic()-t,2))
PY
}
is_running() { docker ps --format '{{.Names}}' | grep -qx "${NAME}"; }
ensure_active() {
  probe || die "llama.cpp not ready; run '$0 logs' and '$0 test' first"
  test_generation || die "Generation test failed; refusing activation"
}

command="${1:-help}"
case "${command}" in
  check)
    require_docker
    echo "Hardware:"; cat /etc/nv_tegra_release 2>/dev/null || true
    echo "RAM:"; free -h
    echo "Available NVMe:"; df -h /mnt/orin-ai 2>/dev/null || true
    echo "GPU:"; command -v tegrastats >/dev/null && command -v timeout >/dev/null && timeout 2 tegrastats --interval 1000 || true
    echo "Active Ollama models (must be unloaded for memory-intensive trials):"
    if command -v ollama >/dev/null; then ollama ps || true; fi
    echo "Managed model:"; docker ps -a --filter "name=^${NAME}$" --format '{{.Names}} {{.Status}}' || true
    ;;
  start)
    require_root; require_docker; check_platform
    model="${2:-gemma}"
    case "${model}" in
      gemma)
        REPO="google/gemma-4-E2B-it-qat-q4_0-gguf"
        FILE="gemma-4-E2B_q4_0-it.gguf"
        CTX=4096
        ;;
      nemotron)
        REPO="nvidia/NVIDIA-Nemotron-3.5-Bolt-E4B-4GB"
        FILE="Nemotron-3.5-Bolt-E4B-Q4_K_M.gguf"
        CTX=2048
        ;;
      *) die "Choose 'gemma' (recommended for 8GB) or 'nemotron' (larger alternative)" ;;
    esac
    if is_running; then die "Managed container is already running; use 'stop' to change models"; fi
    if command -v ollama >/dev/null && ollama ps 2>/dev/null | tail -n +2 | grep -q '[^[:space:]]'; then
       die "Ollama has an active model consuming shared RAM. Unload it deliberately before testing; existing service was not changed."
    fi
    mkdir -p "${CACHE}"
    available_kb=$(df -Pk "${CACHE}" | awk 'NR==2{print $4}')
    (( available_kb >= 7000000 )) || die "Need at least 7 GB free on model cache filesystem"
    docker rm "${NAME}" >/dev/null 2>&1 || true
    docker run -d --name "${NAME}" --restart unless-stopped \
      --runtime nvidia --network host \
      -v "${CACHE}:/root/.cache/huggingface" \
      "${IMAGE}" llama-server \
      -hf "${REPO}" --hf-file "${FILE}" \
      --host 127.0.0.1 --port "${PORT}" --ctx-size "${CTX}" \
      --parallel 1 --n-gpu-layers 999 --jinja --cache-ram 0
    echo "Started ${model}. Downloads/warmup can take time; use 'logs', then 'test'."
    ;;
  logs)
    require_docker
    docker logs --tail 100 "${NAME}"
    ;;
  test)
    probe; test_generation
    ;;
  activate)
    require_root; require_docker
    is_running || die "Model container not running"
    ensure_active
    mkdir -p "${STATE}"
    found=0
    for service in "${SERVICES[@]}"; do
      if ! systemctl cat "${service}" >/dev/null 2>&1; then continue; fi
      found=1
      dest="/etc/systemd/system/${service}.d/90-sentient-jetson-llm.conf"
      mkdir -p "$(dirname "${dest}")"
      if [[ ! -e "${STATE}/${service}.tracked" ]]; then
        if [[ -f "${dest}" ]]; then cp -p "${dest}" "${STATE}/${service}.original"; fi
        touch "${STATE}/${service}.tracked"
      fi
      cat >"${dest}" <<EOF
[Service]
Environment="SENTIENT_LLM_BACKEND=openai"
Environment="SENTIENT_LLM_BASE_URL=${URL}"
EOF
      echo "Configured ${service}"
    done
    (( found > 0 )) || die "No compatible contemplation service unit found (files were not changed)"
    systemctl daemon-reload
    for service in "${SERVICES[@]}"; do
      if systemctl is-active --quiet "${service}"; then systemctl restart "${service}"; fi
    done
    echo "Activated local model backend. Use 'rollback' to restore old config."
    ;;
  rollback)
    require_root
    for service in "${SERVICES[@]}"; do
      dest="/etc/systemd/system/${service}.d/90-sentient-jetson-llm.conf"
      [[ -e "${STATE}/${service}.tracked" ]] || continue
      if [[ -f "${STATE}/${service}.original" ]]; then
        mv "${STATE}/${service}.original" "${dest}"
      else
        rm -f "${dest}"
      fi
      rm -f "${STATE}/${service}.tracked"
    done
    systemctl daemon-reload
    for service in "${SERVICES[@]}"; do
      if systemctl is-active --quiet "${service}"; then systemctl restart "${service}"; fi
    done
    echo "Restored previous inference settings. Existing Ollama installation untouched."
    ;;
  stop)
    require_root; require_docker
    for service in "${SERVICES[@]}"; do
      f="/etc/systemd/system/${service}.d/90-sentient-jetson-llm.conf"
      [[ ! -f "${f}" ]] || die "Model is active in systemd; run 'rollback' before stopping it"
    done
    docker rm -f "${NAME}" >/dev/null 2>&1 || true
    echo "Managed model stopped. Download cache retained for later use."
    ;;
  status)
    require_docker
    docker ps -a --filter "name=^${NAME}$" --format '{{.Names}} {{.Status}}'
    for service in "${SERVICES[@]}"; do
      systemctl is-active "${service}" 2>/dev/null || true
      systemctl show "${service}" -p Environment --no-pager 2>/dev/null || true
    done
    ;;
  *) cat <<EOF
Usage: sudo bash scripts/jetson-models.sh COMMAND
  check           Inspect JetPack, RAM, cache and Ollama usage
  start gemma     Launch Gemma 4 E2B Q4 (8GB default; llama.cpp)
  start nemotron  Launch Nemotron 3.5 Bolt E4B Q4 (heavier alternate)
  logs            Inspect model/container download and warmup
  test            Verify model endpoint and real response
  activate        Opt in existing contemplation service, with rollback state
  rollback        Restore existing Ollama backend and restart service
  stop            Stop only the managed llama.cpp container
  status          Report model and services
Models run one at a time on port 127.0.0.1:18080.
EOF
    ;;
esac

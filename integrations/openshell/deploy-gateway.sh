#!/usr/bin/env bash
# Run only on a separately qualified gateway. No JetPack or Orin service changes.
set -euo pipefail
cd "$(dirname "$0")"
python3 preflight.py
command -v openshell >/dev/null || {
  echo 'Install official NVIDIA OpenShell on gateway: https://docs.nvidia.com/openshell/latest/about/installation/' >&2
  exit 2
}
command -v curl >/dev/null || { echo 'curl required' >&2; exit 2; }
if ! curl --noproxy '*' -fsS --max-time 4 http://127.0.0.1:11435/api/tags >/dev/null; then
  echo 'SSH tunnel not ready: start ssh-tunnel.sh from this gateway first' >&2
  exit 3
fi
docker build -t mazlabz/openshell-orin-diagnostics:1 .
openshell profile lint -f ollama-tunnel.yaml
openshell profile import -f ollama-tunnel.yaml
if ! openshell provider list | grep -q 'mazlabz-orin-ollama'; then
  openshell provider create --name mazlabz-orin-ollama --type mazlabz-orin-ollama
fi
if openshell sandbox list | grep -q 'mazlabz-orin-diagnostics'; then
  echo 'Existing sandbox found; reusing without automatic replacement.'
else
  openshell sandbox create --name mazlabz-orin-diagnostics \
    --from mazlabz/openshell-orin-diagnostics:1 \
    --provider mazlabz-orin-ollama \
    --env OPENAI_BASE_URL=http://host.openshell.internal:11435/v1 \
    -- python3 /opt/mazlabz/diagnostic_agent.py --check
fi
openshell policy get mazlabz-orin-diagnostics --full
openshell sandbox exec --name mazlabz-orin-diagnostics -- python3 /opt/mazlabz/diagnostic_agent.py --check

#!/usr/bin/env bash
# Execute on a compatible Linux Docker gateway, never directly on JetPack 6.
set -euo pipefail
cd "$(dirname "$0")"
python3 preflight.py

command -v curl >/dev/null || { echo 'curl is required' >&2; exit 2; }
if ! command -v openshell >/dev/null; then
  echo 'Installing pinned NVIDIA OpenShell v0.1.5 on qualified gateway'
  curl -LsSf https://raw.githubusercontent.com/NVIDIA/OpenShell/main/install.sh | OPENSHELL_VERSION=v0.1.5 sh
fi
openshell status
bind_ip="$(docker network inspect bridge --format '{{(index .IPAM.Config 0).Gateway}}')"
[[ "$bind_ip" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ && "$bind_ip" != "0.0.0.0" ]] || {
  echo 'Cannot determine safe Docker host-gateway IP' >&2
  exit 2
}
if ! curl --noproxy '*' -fsS --max-time 5 "http://${bind_ip}:11435/api/tags" >/dev/null; then
  echo "SSH tunnel unavailable on Docker host gateway ${bind_ip}:11435." >&2
  echo 'Start ssh-tunnel.sh on THIS gateway with verified Nano SSH credentials.' >&2
  exit 3
fi

docker build -t mazlabz/openshell-orin-diagnostics:1 .
openshell profile lint -f ollama-tunnel.yaml
if openshell profile describe mazlabz-orin-ollama >/dev/null 2>&1; then
  echo 'Reusing existing provider profile'
else
  openshell profile import -f ollama-tunnel.yaml
fi
if ! openshell provider list | grep -q 'mazlabz-orin-ollama'; then
  openshell provider create --name mazlabz-orin-ollama --type mazlabz-orin-ollama
fi
if openshell sandbox list | grep -q 'mazlabz-orin-diagnostics'; then
  echo 'Reusing existing sandbox'
else
  openshell sandbox create --name mazlabz-orin-diagnostics \
    --from mazlabz/openshell-orin-diagnostics:1 \
    --cpu 500m --memory 512Mi \
    --provider mazlabz-orin-ollama \
    --env OPENAI_BASE_URL=http://host.openshell.internal:11435/v1 \
    -- sleep infinity
fi
openshell policy get mazlabz-orin-diagnostics --full
openshell sandbox exec --name mazlabz-orin-diagnostics -- python3 /opt/mazlabz/diagnostic_agent.py --check
echo 'OPENSHELL_ORIN_GATEWAY_VERIFIED'

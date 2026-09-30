#!/usr/bin/env bash
set -euo pipefail

TOKEN="${1:-}"
if [[ -z "$TOKEN" ]]; then
  echo "usage: install_mz_fleet_agent.sh <agent-token>" >&2
  exit 2
fi

ENDPOINT="https://hknyyyudeeemhyugzmja.supabase.co/functions/v1/mz-fleet-relay"
BASE="$HOME/.local/lib/mz-fleet-agent"
CONFIG="$HOME/.config/mz-fleet-agent"
UNIT_DIR="$HOME/.config/systemd/user"

mkdir -p "$BASE" "$CONFIG" "$UNIT_DIR" "$HOME/.local/state/mz-fleet-agent/outbox"
chmod 700 "$CONFIG" "$HOME/.local/state/mz-fleet-agent" "$HOME/.local/state/mz-fleet-agent/outbox"

curl -fsSL   "https://raw.githubusercontent.com/thotsl4yer69/sentient-core/main/tools/mz_fleet_agent.py"   -o "$BASE/agent.py"
chmod 700 "$BASE/agent.py"

cat >"$CONFIG/env" <<EOF
MZ_FLEET_ENDPOINT=$ENDPOINT
MZ_FLEET_TOKEN=$TOKEN
MZ_FLEET_NODE=orin
MZ_FLEET_POLL_SECONDS=4
MZ_FLEET_ACTION_TIMEOUT=120
EOF
chmod 600 "$CONFIG/env"

cat >"$UNIT_DIR/mz-fleet-agent.service" <<EOF
[Unit]
Description=MZ1312 Fleet Remote Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=%h/.config/mz-fleet-agent/env
ExecStart=/usr/bin/python3 %h/.local/lib/mz-fleet-agent/agent.py
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now mz-fleet-agent.service
sleep 2
systemctl --user --no-pager --full status mz-fleet-agent.service || true
echo
echo "MZ_FLEET_AGENT_INSTALLED"

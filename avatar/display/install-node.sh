#!/usr/bin/env bash
# Deploy the hologram renderer from the Pi 500+ onto a Pi 5 display node.
# The node is not turned into a standalone system. Raspberry Pi OS Bookworm,
# Chromium and labwc are expected to be there already.
set -euo pipefail

host=""
user="sc"
dest="/opt/sc/avatar-display"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) host=${2:?}; shift 2 ;;
    --user) user=${2:?}; shift 2 ;;
    --dest) dest=${2:?}; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$host" ]]; then
  echo "usage: avatar/display/install-node.sh --host HOST [--user sc]" >&2
  exit 2
fi

repo=$(cd "$(dirname "$0")/../.." && pwd)
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT

mkdir -p "$stage/www/vendor" "$stage/www/models" "$stage/bin" "$stage/units"
cp "$repo/avatar/renderer/index.html" "$repo/avatar/renderer/config.json" "$stage/www/"
cp -a "$repo/avatar/renderer/vendor/." "$stage/www/vendor/"
cp -a "$repo/avatar/renderer/models/." "$stage/www/models/"
rm -rf "$stage/www/vendor/node_modules" "$stage/www/node_modules"
cp "$repo/avatar/display/kiosk.sh" "$repo/avatar/display/static_server.py" "$stage/bin/"
cp "$repo/avatar/display/sc-avatar-static.service" \
   "$repo/avatar/display/sc-avatar-kiosk.service" \
   "$repo/avatar/display/sc.target" \
   "$stage/units/"
chmod 755 "$stage/bin/kiosk.sh" "$stage/bin/static_server.py"

ssh_opts=(-o BatchMode=yes -o StrictHostKeyChecking=yes)
remote=${user}@${host}

ssh "${ssh_opts[@]}" "$remote" "sudo -n id '$user' >/dev/null 2>&1 || sudo -n useradd --system --create-home --shell /usr/sbin/nologin '$user'"
ssh "${ssh_opts[@]}" "$remote" "command -v labwc >/dev/null && { command -v chromium >/dev/null || command -v chromium-browser >/dev/null; }"

tar -C "$stage" -czf - . | ssh "${ssh_opts[@]}" "$remote" "sudo -n mkdir -p '$dest' && sudo -n tar -C '$dest' -xzf - && sudo -n chown -R '$user:$user' '$dest'"

ssh "${ssh_opts[@]}" "$remote" "sudo -n bash -s" <<EOF
set -euo pipefail
if [[ ! -f /etc/systemd/system/sc.target ]]; then
  install -m 0644 '$dest/units/sc.target' /etc/systemd/system/sc.target
fi
install -m 0644 '$dest/units/sc-avatar-static.service' /etc/systemd/system/sc-avatar-static.service
install -m 0644 '$dest/units/sc-avatar-kiosk.service' /etc/systemd/system/sc-avatar-kiosk.service
if [[ -f /etc/sc/avatar-display.json ]]; then
  python3 - <<'PY'
import json
from pathlib import Path
extra = json.loads(Path("/etc/sc/avatar-display.json").read_text())
path = Path("$dest/www/config.json")
base = json.loads(path.read_text())
base.update(extra)
path.write_text(json.dumps(base, indent=2) + "\n")
PY
  chown '$user:$user' '$dest/www/config.json'
fi
systemctl daemon-reload
systemctl enable sc.target sc-avatar-static.service sc-avatar-kiosk.service
systemctl restart sc-avatar-static.service
systemctl restart sc-avatar-kiosk.service
EOF

echo "deployed avatar display to ${host}:${dest}"

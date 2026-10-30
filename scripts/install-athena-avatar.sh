#!/usr/bin/env bash
# Download verified ATHENA skinned GLB locally, preserving existing orb UI.
set -Eeuo pipefail
BASE="${SENTIENT_ROOT:-/opt/sentient-core}"
ASSET_DIR="${BASE}/interfaces/web_chat/static/models"
FILE="${ASSET_DIR}/athena.glb"
URL='https://d2ol7oe51mr4n9.cloudfront.net/user_3J2eeA6Q5aKeLXqP7Sh07VXpkjX/27960480-95de-44de-b869-30080b584828.glb'
SHA256='965246a711760df5028c1b81ba03fe4d8638946d614f345ab39f3acd2bff67d2'
command -v curl >/dev/null || { echo 'curl required' >&2;exit 1; }
[[ -d "${BASE}/interfaces/web_chat/static" ]] || { echo "Missing Sentient Core frontend: ${BASE}" >&2;exit 1; }
mkdir -p "${ASSET_DIR}"
tmp="$(mktemp "${ASSET_DIR}/.athena.XXXXXX")"
trap 'rm -f "${tmp}"' EXIT
curl -fL --retry 3 --connect-timeout 15 --max-time 180 "${URL}" -o "${tmp}"
echo "${SHA256}  ${tmp}" | sha256sum --check --status || { echo "Checksum mismatch; old avatar preserved" >&2;exit 1; }
python3 - "${tmp}" <<'PY'
import json,struct,sys
with open(sys.argv[1],'rb') as f:
 h=f.read(20)
 assert h[:4]==b'glTF' and struct.unpack_from('<I',h,4)[0]==2
 n=struct.unpack_from('<I',h,12)[0]
 j=json.loads(f.read(n))
 assert len(j.get('skins',[]))>=1
 assert len(j['skins'][0]['joints'])>=100
 assert len(j.get('animations',[]))>=1
 assert j.get('meshes') and j.get('scenes')
 print(f"Valid GLB: {len(j['skins'][0]['joints'])} bones, {len(j['animations'])} clips")
PY
if [[ -f "${FILE}" ]]; then cp -p "${FILE}" "${FILE}.backup"; fi
chmod 644 "${tmp}"
mv -f "${tmp}" "${FILE}"
echo "ATHENA installed: ${FILE}"
echo 'Viewer: http://<orin-lan-address>:3001/static/athena.html'
echo 'Existing orb interface unchanged. No service restart required.'

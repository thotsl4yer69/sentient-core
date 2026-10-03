#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
command -v node >/dev/null
command -v npm >/dev/null
command -v python3 >/dev/null
command -v curl >/dev/null
UPSTREAM=b3e277b3b46f88e557bf28a2c5612a5b04e075c3
BASE="https://raw.githubusercontent.com/met4citizen/TalkingHead/$UPSTREAM"
mkdir -p vendor models node_modules
fetch() { curl --fail --location --retry 2 --connect-timeout 15 "$1" -o "$2"; }
for file in talkinghead retargeter dynamicbones; do fetch "$BASE/modules/$file.mjs" "vendor/$file.mjs"; done
cat <<'HASHES' | sha256sum --check
2580bfb965f432e3c6eed0115417d5a2814f4ad0689e031a0977122b42817f6b  vendor/talkinghead.mjs
aae6954f90f35263d6116737b3e34a202f3f1430ffd1ce1368f7af7d2e8774cd  vendor/retargeter.mjs
e3e754a8c4296b470a763be3705694d10dfa00cb7a0b311fa6d4751297ab8d82  vendor/dynamicbones.mjs
HASHES
npm install --ignore-scripts --no-save --no-package-lock three@0.180.0 esbuild@0.25.10
fetch "$BASE/avatars/mpfb.glb" models/mpfb-source.glb
printf '%s\n' '63c645a2a863b9972e9a9c2ed576a1de4c390b8475508e1473e69c87a3ee299c  models/mpfb-source.glb' | sha256sum --check
fetch "$BASE/README.md" MODEL_SOURCE_README.md
node --test tests/protocol.test.mjs
./node_modules/.bin/esbuild src/runtime.mjs --bundle --format=esm --target=chrome110 --minify --outfile=athena.bundle.mjs
# Preserve the skeleton, facial shapes and named meshes. Only geometry packing and textures change.
npx --yes @gltf-transform/cli@4.2.1 optimize models/mpfb-source.glb models/athena.glb --compress meshopt --texture-compress webp --texture-size 1024 --simplify false --flatten false --join false --palette false --instance false
python3 tools/manage.py audit models/athena.glb > asset-audit.json
mkdir -p licenses
cp node_modules/three/LICENSE licenses/THREE-LICENSE.txt
python3 - <<'PY'
from pathlib import Path
source=Path('vendor/talkinghead.mjs').read_text()
Path('licenses/TALKINGHEAD-LICENSE.txt').write_text(source.split('*/',1)[0]+'*/\n')
PY
printf '\nBuilt real 3D assets. Preview: python3 -m http.server 8765 --bind 127.0.0.1\n'
printf 'Open http://127.0.0.1:8765/index.html?preview=1\n'

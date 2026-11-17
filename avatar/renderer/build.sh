#!/usr/bin/env bash
# Rebuild the offline bundle from the pinned TalkingHead sources and three@0.180.0.
set -euo pipefail
cd "$(dirname "$0")"
sha256sum -c vendor/UPSTREAM.sha256
npm install --ignore-scripts --no-fund --no-audit
npx esbuild src/main.mjs \
  --bundle \
  --format=esm \
  --target=chrome120 \
  --minify \
  --keep-names \
  --legal-comments=none \
  --outfile=vendor/avatar.bundle.mjs
cp node_modules/three/LICENSE vendor/THREE-LICENSE.txt
echo "bundle $(sha256sum vendor/avatar.bundle.mjs)"

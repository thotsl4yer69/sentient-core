#!/usr/bin/env bash
# Meshopt + WebP pass for a Tripo/Blender GLB. Pins the CLI that produced the test avatar.
set -euo pipefail

if [[ $# -lt 2 || $# -gt 3 ]]; then
  echo "usage: avatar/tools/optimise_glb.sh INPUT.glb OUTPUT.glb [texture-size]" >&2
  exit 2
fi

input=$1
output=$2
texture_size=${3:-2048}
cli_version=4.2.1

if ! command -v npx >/dev/null 2>&1; then
  echo "npx is required to run @gltf-transform/cli@${cli_version}" >&2
  exit 1
fi

npx --yes "@gltf-transform/cli@${cli_version}" optimize "$input" "$output" \
  --compress meshopt \
  --texture-compress webp \
  --texture-size "$texture_size" \
  --simplify false \
  --flatten false \
  --join false \
  --palette false \
  --instance false

echo "wrote ${output} (meshopt, webp, texture ${texture_size}px, triangles unchanged)"

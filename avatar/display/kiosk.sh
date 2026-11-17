#!/bin/sh
# Wayland kiosk for the Pi 5 hologram. Raspberry Pi OS Bookworm, labwc, Chromium.
set -eu

runtime="${XDG_RUNTIME_DIR:-/run/sc-avatar}"
export XDG_RUNTIME_DIR="$runtime"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-1}"
export WLR_LIBINPUT_NO_DEVICES=1
mkdir -p "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"

browser=""
for candidate in /usr/bin/chromium /usr/bin/chromium-browser; do
  if [ -x "$candidate" ]; then
    browser=$candidate
    break
  fi
done
if [ -z "$browser" ]; then
  echo "chromium is not installed on this display node" >&2
  exit 1
fi
if ! command -v labwc >/dev/null 2>&1; then
  echo "labwc is not installed on this display node" >&2
  exit 1
fi

debug=""
if [ -n "${SC_AVATAR_DEBUG_PORT:-}" ]; then
  debug="--remote-debugging-port=${SC_AVATAR_DEBUG_PORT} --remote-debugging-address=127.0.0.1"
fi

exec labwc -s "$browser --kiosk --ozone-platform=wayland \
  --enable-gpu --ignore-gpu-blocklist --enable-gpu-rasterization \
  --use-gl=angle --use-angle=gles-egl \
  --autoplay-policy=no-user-gesture-required \
  --noerrdialogs --disable-infobars --disable-session-crashed-bubble \
  --overscroll-history-navigation=0 \
  --check-for-update-interval=31536000 \
  $debug \
  http://127.0.0.1:8090/"

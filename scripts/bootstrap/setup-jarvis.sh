#!/bin/bash
set -euo pipefail

MANIFEST_URL=${MANIFEST_URL:-"https://github.com/yar020052-ship-it/Jarvis/releases/latest/download/latest.json"}
CHANNEL=${CHANNEL:-"stable"}

echo "[INFO] JARVIS bootstrapper (Unix)"

OS=$(uname -s | tr '[:upper:]' '[:lower:]')
ARCH=$(uname -m)
case "$ARCH" in
  x86_64|amd64) ARCH_KEY="x64" ;;
  aarch64|arm64) ARCH_KEY="arm64" ;;
  *) ARCH_KEY="$ARCH" ;;
esac

case "$OS" in
  linux) PLAT="linux-$ARCH_KEY" ;;
  darwin) PLAT="darwin-$ARCH_KEY" ;;
  *) PLAT="$OS-$ARCH_KEY" ;;
esac
echo "[INFO] Platform: $PLAT"

TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT

# manifest
echo "[INFO] Fetching manifest..."
MANIFEST=$(curl -sL --connect-timeout 15 "$MANIFEST_URL")
VER=$(echo "$MANIFEST" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('version',''))")
echo "[OK] Latest: $VER"

# app dir
case "$OS" in
  linux) APP_DIR="$HOME/.local/share/Jarvis"; mkdir -p "$APP_DIR" ;;
  darwin) APP_DIR="$HOME/Library/Application Support/Jarvis"; mkdir -p "$APP_DIR" ;;
  *) APP_DIR="$HOME/.jarvis"; mkdir -p "$APP_DIR" ;;
esac

# get platform info
PINFO=$(echo "$MANIFEST" | python3 -c "
import sys,json
d=json.load(sys.stdin)
p=d['platforms']
k='$PLAT'
v=p.get(k) or p.get(k.replace('-','_'))
if not v: sys.exit(1)
print(v['url']+'\n'+v.get('sha256',''))
")
readarray -t arr <<< "$PINFO"
URL=${arr[0]}
SHA=${arr[1]:-}

ZIP="$TMP_DIR/Jarvis-$VER-$PLAT.zip"
echo "[INFO] Downloading $URL"
curl -L --connect-timeout 60 -o "$ZIP" "$URL"
echo "[OK] Downloaded"

# SHA256
if [ -n "$SHA" ]; then
  ACTUAL=$(python3 -c "import hashlib;print(hashlib.sha256(open('$ZIP','rb').read()).hexdigest())")
  if [ "${ACTUAL,,}" != "${SHA,,}" ]; then
    echo "[ERR] SHA256 mismatch"; exit 1
  fi
  echo "[OK] SHA256 verified"
fi

# update or install
UPD="$APP_DIR/scripts/update.py"
if [ ! -f "$UPD" ]; then
  unzip -q "$ZIP" -d "$APP_DIR"
  echo "[OK] Installed"
else
  PYTHON="python3"
  "$PYTHON" "$UPD" --app-dir "$APP_DIR" --zip "$ZIP"
fi

echo "[OK] Done. App dir: $APP_DIR"
echo "[INFO] Run: python3 $APP_DIR/main.py"

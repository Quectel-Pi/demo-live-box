#!/bin/bash
# 把 demo_live_box 部署到 adb 连接的板端 (/opt/demo_live_box)
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
REMOTE=/opt/demo_live_box
TMP=/tmp/demo_live_box.tar.gz

FILES="main.py camera.py streamer.py webui.py ui.py selftest.py config.json run.sh README.md deploy/board_setup.sh"

echo "==> Checking adb devices"
adb devices | grep -w device >/dev/null || { echo "No adb device found"; exit 1; }

echo "==> Packaging and pushing"
tar -C "$HERE" -czf "$TMP" $FILES
adb shell "mkdir -p $REMOTE/records $REMOTE/deploy"
adb push "$TMP" /tmp/demo_live_box.tar.gz >/dev/null
adb shell "tar -xzf /tmp/demo_live_box.tar.gz -C $REMOTE && \
           chmod +x $REMOTE/run.sh && rm -f /tmp/demo_live_box.tar.gz"
rm -f "$TMP"

echo "==> Done: adb shell $REMOTE/run.sh"

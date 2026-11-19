#!/bin/sh
# 板端环境检查（在板子上以 root 执行）：sh board_setup.sh
echo "== System =="
uname -m
cat /etc/os-release | grep PRETTY_NAME

echo "== Dependencies =="
for cmd in ffmpeg ffprobe v4l2-ctl arecord python3; do
    if command -v "$cmd" >/dev/null 2>&1; then
        echo "  OK   $cmd"
    else
        echo "  MISS $cmd"
    fi
done
python3 -c "import cv2; print('  OK   opencv', cv2.__version__)" 2>&1 \
    || echo "  MISS python3-opencv"

echo "== USB camera =="
ls -l /dev/v4l/by-id/*video-index0 2>/dev/null || echo "  No UVC camera found"

echo "== Audio input =="
arecord -l 2>/dev/null | head -10 || true

echo "== Display =="
ls /sys/class/drm/card*-*/status 2>/dev/null | while read -r f; do
    printf "  %s %s\n" "$f" "$(cat "$f")"
done

echo "== Ports =="
ss -ltn 2>/dev/null | grep -E ":8080\b" || echo "  8080 is free"

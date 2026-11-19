#!/bin/sh
# 板端环境检查（在板子上以 root 执行）：sh board_setup.sh
echo "== 系统 =="
uname -m
cat /etc/os-release | grep PRETTY_NAME

echo "== 依赖 =="
for cmd in ffmpeg ffprobe v4l2-ctl arecord python3; do
    if command -v "$cmd" >/dev/null 2>&1; then
        echo "  OK   $cmd"
    else
        echo "  MISS $cmd"
    fi
done
python3 -c "import cv2; print('  OK   opencv', cv2.__version__)" 2>&1 \
    || echo "  MISS python3-opencv"

echo "== USB 摄像头 =="
ls -l /dev/v4l/by-id/*video-index0 2>/dev/null || echo "  未发现 UVC 摄像头"

echo "== 音频输入 =="
arecord -l 2>/dev/null | head -10 || true

echo "== 显示 =="
ls /sys/class/drm/card*-*/status 2>/dev/null | while read -r f; do
    printf "  %s %s\n" "$f" "$(cat "$f")"
done

echo "== 端口 =="
ss -ltn 2>/dev/null | grep -E ":8080\b" || echo "  8080 未占用"

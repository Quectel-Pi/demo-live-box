#!/bin/sh
# demo_live_box 启动脚本 (板端 root 终端用)
# 用法: ./run.sh              有显示屏时自动打开本地预览窗口 + Web 控制台
#       ./run.sh --no-ui      纯无界面 (适合无显示屏盒子)
#       ./run.sh --start      启动后自动推流
# 原理: 桌面会话是普通用户(q)的 Wayland, root 需借用其 XWayland 授权才能开窗口
export DISPLAY=${DISPLAY:-:0}
XAUTH=$(ls /run/user/*/.mutter-Xwaylandauth.* 2>/dev/null | head -1)
[ -n "$XAUTH" ] && export XAUTHORITY="$XAUTH"
cd "$(dirname "$0")" || exit 1
exec python3 main.py "$@"

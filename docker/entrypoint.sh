#!/usr/bin/env bash
# 容器入口：起虚拟屏幕和 VNC，再让 ccs 打开浏览器窗口。
#
# 环境变量：
#   CCS_NAME    沙盒名字，默认 sandbox
#   CCS_EXIT    新沙盒的出口，例如 socks5h://172.17.0.1:7897
#   CCS_SCREEN  Xvfb 分辨率，默认 2560x1440x24，要和配置里的 screen 一致
set -eu

export DISPLAY="${DISPLAY:-:99}"
screen="${CCS_SCREEN:-2560x1440x24}"

Xvfb "$DISPLAY" -screen 0 "$screen" -nolisten tcp >/tmp/xvfb.log 2>&1 &
xvfb_pid=$!

# 等 X socket 出现，最多 5 秒
x_socket="/tmp/.X11-unix/X${DISPLAY#:}"
for _ in $(seq 1 50); do
    [ -S "$x_socket" ] && break
    sleep 0.1
done
if [ ! -S "$x_socket" ]; then
    echo "Xvfb 没起来，看 /tmp/xvfb.log" >&2
    kill "$xvfb_pid" 2>/dev/null || true
    exit 1
fi

x11vnc -display "$DISPLAY" -forever -shared -nopw -rfbport 5900 \
    -listen 0.0.0.0 >/tmp/x11vnc.log 2>&1 &
websockify --web=/usr/share/novnc 6080 127.0.0.1:5900 >/tmp/websockify.log 2>&1 &
echo "noVNC: http://localhost:6080/vnc.html"

name="${CCS_NAME:-sandbox}"
if [ ! -d "$HOME/sandboxes/$name" ]; then
    if [ -n "${CCS_EXIT:-}" ]; then
        ccs new "$name" --exit "$CCS_EXIT"
    else
        ccs new "$name"
    fi
fi

exec ccs web "$name"

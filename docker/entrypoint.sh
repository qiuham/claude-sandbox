#!/bin/sh
# 沙盒容器入口：虚拟屏幕 + x11vnc + noVNC，然后把浏览器窗口开在这块屏幕上。
# 网页入口: http://<容器>:6080/vnc.html（建议只经 SSH 隧道访问，别暴露公网）
set -eu

PROFILE="${SANDBOX_PROFILE:?set SANDBOX_PROFILE}"
EXIT="${SANDBOX_EXIT:-socks5h://172.18.0.1:7871}"
DISPLAY_NUM="${SANDBOX_DISPLAY:-:99}"
GEOMETRY="${SANDBOX_GEOMETRY:-1440x900x24}"

Xvfb "$DISPLAY_NUM" -screen 0 "$GEOMETRY" &
sleep 1
x11vnc -display "$DISPLAY_NUM" -forever -shared -nopw -listen localhost -rfbport 5900 -bg -o /tmp/x11vnc.log
websockify --web /usr/share/novnc 0.0.0.0:6080 localhost:5900 &
sleep 1

export DISPLAY="$DISPLAY_NUM"
export SANDBOX_ROOT="${SANDBOX_ROOT:-/data}"

echo "沙盒已启动：profile=$PROFILE exit=$EXIT display=$DISPLAY_NUM"
echo "noVNC: http://127.0.0.1:6080/vnc.html?autoconnect=1&resize=scale"
exec ccs start "$PROFILE" --exit "$EXIT"

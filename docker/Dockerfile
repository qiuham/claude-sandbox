# 沙盒镜像：容器里跑浏览器窗口，通过 noVNC 用网页操作。
#
# 基础镜像用池子的 worker 镜像：里面已经有权改版 Firefox（invisible_playwright）、
# 浏览器引擎和 Xvfb，这里只补 VNC 和沙盒脚本。
# 注意：本机 docker 拉不到 docker.io 的基础镜像，所以默认用本地已有的 worker 镜像；
# 换机器构建时把 BASE 指到能拉到的等价镜像，或先 docker load 一个 worker 镜像。
ARG BASE=accountpool-worker:20261017-r31
FROM ${BASE}

USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
        x11vnc novnc websockify \
    && rm -rf /var/lib/apt/lists/*

COPY sandbox /usr/local/bin/sandbox
COPY docker/entrypoint.sh /usr/local/bin/sandbox-entrypoint
RUN chmod 755 /usr/local/bin/sandbox /usr/local/bin/sandbox-entrypoint \
    && mkdir -p /data && chown 1000:1000 /data

ENV SANDBOX_ROOT=/data \
    SANDBOX_PROFILE=demo \
    SANDBOX_EXIT=socks5h://172.18.0.1:7871
EXPOSE 6080
ENTRYPOINT ["sandbox-entrypoint"]

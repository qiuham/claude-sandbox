# 沙盒镜像：容器里跑 Firefox 窗口，用浏览器打开 noVNC 操作。
#
# 不依赖内部镜像。基础镜像用公开的 python:3.13-slim，依赖、引擎、系统库
# 全部交给仓库自己的 install.sh，不维护第二份安装逻辑。

FROM python:3.13-slim

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    DISPLAY=:99 \
    PATH=/home/sandbox/.local/bin:${PATH}

# Xvfb 提供虚拟屏幕，x11vnc + noVNC 把屏幕搬到浏览器里
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl procps sudo \
        xvfb x11vnc novnc websockify \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /bin/bash sandbox \
    && printf 'sandbox ALL=(ALL) NOPASSWD:ALL\n' > /etc/sudoers.d/sandbox \
    && chmod 0440 /etc/sudoers.d/sandbox

USER sandbox
WORKDIR /home/sandbox/app

COPY --chown=sandbox:sandbox . .

# install.sh --auto：装 uv、依赖、浏览器引擎，缺的系统库直接 apt 装
RUN ./install.sh --auto --no-link

EXPOSE 6080

ENTRYPOINT ["/home/sandbox/app/docker/entrypoint.sh"]

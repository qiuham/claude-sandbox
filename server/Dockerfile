# 同步服务：单文件、只用标准库，所以镜像很小。
# 用法见同目录的 compose.yaml。

FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    CCS_CLOUD_DATA=/data \
    CCS_CLOUD_PORT=8787

WORKDIR /app
COPY ccs_cloud.py /app/ccs_cloud.py

RUN useradd --create-home --uid 10001 cloud \
    && mkdir -p /data && chown cloud:cloud /data
USER cloud

VOLUME ["/data"]
EXPOSE 8787

CMD ["python", "/app/ccs_cloud.py"]

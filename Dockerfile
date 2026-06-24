# syntax=docker/dockerfile:1

FROM python:3.12-slim

LABEL org.opencontainers.image.title="WuYou（一坞邮）" \
      org.opencontainers.image.description="跨平台 Docker 部署的多邮箱 Web 管理工具。聚合收件箱、标签、翻译、插件社区、主题语言包、PGP 加密、CalDAV/CardDAV 同步。" \
      org.opencontainers.image.version="1.0.1" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.source="https://github.com/planover/WuYou" \
      org.opencontainers.image.url="https://github.com/planover/WuYou" \
      org.opencontainers.image.documentation="https://github.com/planover/WuYou" \
      org.opencontainers.image.authors="planover" \
      org.opencontainers.image.vendor="WuYou Team"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WUYOU_ENVIRONMENT=production \
    WUYOU_DATA_DIR=/data

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

COPY backend /app/backend
COPY plugin-community /app/plugin-community
COPY language-packs /app/language-packs
COPY theme-packs /app/theme-packs

WORKDIR /app/backend

EXPOSE 8000
VOLUME ["/data"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

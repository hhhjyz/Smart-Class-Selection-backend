# 单镜像，api / worker 通过 MODE 环境变量区分入口。
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install .

COPY app ./app
COPY migrations ./migrations

EXPOSE 8003

# MODE=api 启 uvicorn；MODE=worker 启后台任务调度
CMD ["python", "-m", "app.main"]

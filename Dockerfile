FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST_SYS=/host/sys \
    HOST_PROC=/host/proc \
    DOCKER_CONTAINERS_ROOT=/host/docker/containers \
    CONFIG_PATH=/config/config.json \
    POLL_INTERVAL=1 \
    HISTORY_MINUTES=60 \
    PROCESS_LIMIT=18

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app /app

# Runs as root only so it can read host /proc/<pid>/io and cgroup metadata.
# Host mounts remain read-only and all capabilities are dropped except SYS_PTRACE at deployment.

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2).read()" || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]

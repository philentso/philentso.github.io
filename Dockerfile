FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
ENV NAMESPACE=task-pipeline \
    DISPLAY_TZ=Asia/Shanghai \
    REFRESH_INTERVAL=5 \
    PYTHONUNBUFFERED=1
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz')"
# 注意：只能 1 个 worker（后台刷新线程在每个进程里各跑一份，多 worker 只会重复打 k8s API）
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]

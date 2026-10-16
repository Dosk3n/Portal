FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY app/ /app/
RUN mkdir -p /data /storage && chown -R 1000:1000 /data /storage
USER 1000:1000
EXPOSE 8769
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD ["python3", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8769/health',timeout=4).read()"]
CMD ["python3", "/app/server.py", "--host", "0.0.0.0", "--data-dir", "/data", "--storage-dir", "/storage"]

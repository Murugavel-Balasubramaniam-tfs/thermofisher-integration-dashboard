FROM techops-docker-prod.ha-us.dso.thermofisher.net/python:3.10-slim

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY application.yml server.py ./
COPY static ./static

RUN mkdir -p /app/logs /app/data \
    && chown -R nobody:nogroup /app

USER nobody

ENV HOST=0.0.0.0 \
    PORT=8000 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/integration-dashboard/api/health', timeout=3)" || exit 1

CMD ["python", "-u", "server.py"]

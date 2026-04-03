FROM python:3.12-slim

RUN groupadd -g 10001 app && useradd -m -u 10001 -g 10001 -s /usr/sbin/nologin app

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=app:app . .

USER app

EXPOSE 2101
EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=3s --retries=3 CMD python - <<'PY' || exit 1
import socket,sys
host='127.0.0.1'
port=2101
s=socket.socket()
s.settimeout(2)
try:
    s.connect((host,port))
    s.sendall(b"GET /healthz HTTP/1.0\r\n\r\n")
    buf=s.recv(64)
    sys.exit(0 if b"200" in buf else 1)
except Exception:
    sys.exit(1)
PY

CMD ["python", "main.py", "--config", "config/caster_config.json"]

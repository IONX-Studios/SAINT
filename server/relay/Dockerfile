# Halo Relay. Build from the repository root so the shared protocol code and the web client are included:
#     docker build -f server/relay/Dockerfile -t halo-relay .
FROM python:3.12-slim
WORKDIR /app
COPY server/relay/requirements.txt server/relay/requirements.txt
RUN pip install --no-cache-dir -r server/relay/requirements.txt
# only what the relay needs: the protocol (and the Link crypto it uses), the relay, the browser client
COPY modules/__init__.py modules/__init__.py
COPY modules/link/__init__.py modules/link/crypto.py modules/link/
COPY modules/remote_desktop/__init__.py modules/remote_desktop/protocol.py modules/remote_desktop/
COPY server/__init__.py server/__init__.py
COPY server/relay/__init__.py server/relay/relay.py server/relay/
COPY web/remote web/remote
RUN useradd --system --no-create-home relay
USER relay
ENV HALO_RELAY_PORT=8080 PYTHONUNBUFFERED=1
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz')"
CMD ["python", "-m", "server.relay.relay"]

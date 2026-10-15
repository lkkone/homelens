ARG NODE_IMAGE=node:22-alpine
ARG PYTHON_IMAGE=python:3.13-alpine3.21
FROM --platform=$BUILDPLATFORM ${NODE_IMAGE} AS frontend
WORKDIR /web
COPY frontend/package*.json ./
RUN unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy && npm ci
COPY frontend/ ./
RUN npm run build
FROM ${PYTHON_IMAGE} AS backend-check
ENV PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY server/ ./server/
COPY tests/ ./tests/
RUN python -m unittest discover -s tests
FROM ${PYTHON_IMAGE}
ARG INSTALL_SPEEDTEST=false
ARG TARGETARCH
RUN unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy && apk add --no-cache tzdata
RUN unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy; \
    if [ "$INSTALL_SPEEDTEST" = "true" ]; then \
      case "$TARGETARCH" in \
        arm64) speed_arch=aarch64; speed_sha=3953d231da3783e2bf8904b6dd72767c5c6e533e163d3742fd0437affa431bd3 ;; \
        amd64) speed_arch=x86_64; speed_sha=5690596c54ff9bed63fa3732f818a05dbc2db19ad36ed68f21ca5f64d5cfeeb7 ;; \
        *) echo "Speedtest CLI supports amd64 and arm64 in this image"; exit 1 ;; \
      esac; \
      wget -q -O /tmp/speedtest.tgz "https://install.speedtest.net/app/cli/ookla-speedtest-1.2.0-linux-${speed_arch}.tgz" && \
      echo "${speed_sha}  /tmp/speedtest.tgz" | sha256sum -c - && \
      tar -xzf /tmp/speedtest.tgz -C /usr/local/bin speedtest && rm /tmp/speedtest.tgz; \
    fi
RUN addgroup -S -g 10001 homelens && adduser -S -u 10001 -G homelens homelens && mkdir /data && chown homelens:homelens /data
WORKDIR /app
COPY --from=backend-check /app/server ./server
COPY --from=frontend /web/dist ./server/web
ENV DATA_DIR=/data BIND_ADDRESS=0.0.0.0 PORT=8080 PYTHONDONTWRITEBYTECODE=1
USER homelens
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD python -c "import urllib.request; urllib.request.build_opener(urllib.request.ProxyHandler({})).open('http://127.0.0.1:8080/healthz', timeout=3).close()"
CMD ["python", "server/app.py"]

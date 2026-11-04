FROM node:24-bookworm-slim AS web-build
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.13-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SHARED_EXPENSE_DB=/data/ledger.sqlite3
WORKDIR /app
COPY deploy/requirements.txt ./deploy/requirements.txt
RUN pip install --no-cache-dir --require-hashes -r deploy/requirements.txt \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home app \
    && mkdir /data \
    && chown app:app /data
COPY server/ ./server/
COPY scripts/ ./scripts/
COPY --from=web-build /build/web/dist/ ./web/dist/
USER app
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=3).close()"
CMD ["python", "-m", "uvicorn", "server.main:application", "--factory", "--host", "0.0.0.0", "--port", "8765", "--workers", "1", "--no-access-log"]

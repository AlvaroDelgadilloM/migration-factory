FROM python:3.12.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN useradd --uid 10001 --create-home mf && mkdir -p /artifacts /sources && chown 10001 /artifacts
WORKDIR /app/backend
COPY backend/requirements.lock ./
RUN pip install -r requirements.lock
COPY backend/ ./
COPY rules/ /app/rules/
ENV MF_RULES_CATALOG=/app/rules/catalog.json
USER 10001
EXPOSE 8000
CMD ["sh", "-c", "python -m mf.cli init && python -m mf.cli demo-project && exec uvicorn mf.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'"]

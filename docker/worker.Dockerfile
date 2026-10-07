# JDK 21 + Maven 3.9.11 (pinned) + Python 3.12 for the orchestration code. No Docker socket, non-root.
FROM maven:3.9.11-eclipse-temurin-21-noble
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv git util-linux \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/venv \
    && useradd --uid 10001 --create-home mf \
    && mkdir -p /artifacts /sources /work /m2 && chown 10001 /artifacts /work /m2
WORKDIR /app/backend
COPY backend/requirements.lock ./
RUN /opt/venv/bin/pip install -r requirements.lock
COPY backend/ ./
COPY rules/ /app/rules/
COPY examples/ /app/examples/
ENV PATH=/opt/venv/bin:$PATH MF_RULES_CATALOG=/app/rules/catalog.json MAVEN_HOME=/usr/share/maven
USER 10001
# The base image entrypoint writes to /root/.m2; not needed (maven.repo.local is set per run).
ENTRYPOINT []
CMD ["python", "-m", "mf.worker.main"]

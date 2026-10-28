FROM eclipse-temurin:25.0.4.1_1-jdk-noble AS jdk25
# JDK 21 + Maven 3.9.11 (pinned) + Python 3.12 for the orchestration code. No Docker socket, non-root.
FROM maven:3.9.11-eclipse-temurin-21-noble
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv git util-linux curl postgresql-client \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/venv \
    && useradd --uid 10001 --create-home mf \
    && mkdir -p /artifacts /sources /work /m2 /odoo-addons && chown 10001 /artifacts /work /m2 /odoo-addons
# Second JDK for migrations chosen with Java 25 (mf.versions): the official Temurin 25.0.4.1 image, pinned and multi-arch
# (amd64 on the VPS, arm64 on Apple silicon). The default JDK (JAVA_HOME, from the base image) stays 21; the worker uses this
# one only to build and run Java 25 results.
COPY --from=jdk25 /opt/java/openjdk /opt/jdk-25
RUN /opt/jdk-25/bin/java -version
# Screenshots of the test evidence (mf.tomcat_war.evidence): Playwright's Chromium, pinned, with its system libraries. Ubuntu's
# own chromium is a snap stub that does not run in a container and Google Chrome has no linux/arm64 build; Playwright publishes
# both. Only the browser stays: the downloader venv is removed. It opens only the local evidence pages the worker writes.
RUN python3 -m venv /tmp/pw && /tmp/pw/bin/pip install "playwright==1.49.1" \
    && PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright /tmp/pw/bin/playwright install --with-deps chromium \
    && ln -s "$(ls -d /opt/ms-playwright/chromium-*/chrome-linux/chrome | head -1)" /usr/local/bin/chromium \
    && rm -rf /tmp/pw /var/lib/apt/lists/* \
    && chromium --version
# Dependency vulnerability scan (mf.vulnscan): osv-scanner, pinned and verified against its published SHA256SUMS. It downloads the
# public OSV database (Maven) into the job workspace and matches offline: the dependency inventory never leaves the worker.
ARG TARGETARCH
RUN case "$TARGETARCH" in \
      amd64) sum=160c6253e2922e9e0a6a9771c5c4c40a4b3c6090b34a3cdd5e80c96cc2e4218b ;; \
      arm64) sum=4061ef4c021ed3c47f203b818f337e351336cc5cd67c0274fc36bd1dfc728dd7 ;; \
      *) echo "arquitectura no soportada: $TARGETARCH" && exit 1 ;; esac \
    && curl -fsSL -o /usr/local/bin/osv-scanner "https://github.com/google/osv-scanner/releases/download/v2.7.0/osv-scanner_linux_${TARGETARCH}" \
    && echo "$sum  /usr/local/bin/osv-scanner" | sha256sum -c - && chmod 755 /usr/local/bin/osv-scanner && osv-scanner --version
# DataStage -> Airflow DAG_PARSE_GATE: Apache Airflow in its own venv (pinned with the official constraints), used only to
# import the GENERATED DAGs with DagBag in a separate process; the platform venv never imports Airflow.
RUN python3 -m venv /opt/airflow-venv \
    && /opt/airflow-venv/bin/pip install "apache-airflow==2.10.5" "apache-airflow-providers-oracle==4.0.0" \
       "apache-airflow-providers-common-sql==1.21.0" \
       --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-2.10.5/constraints-3.12.txt"
WORKDIR /app/backend
COPY backend/requirements.lock ./
RUN /opt/venv/bin/pip install -r requirements.lock
COPY backend/ ./
COPY rules/ /app/rules/
COPY examples/ /app/examples/
ENV PATH=/opt/venv/bin:$PATH MF_RULES_CATALOG=/app/rules/catalog.json MAVEN_HOME=/usr/share/maven \
    MF_AIRFLOW_PYTHON=/opt/airflow-venv/bin/python MF_JDK25_HOME=/opt/jdk-25 \
    MF_BROWSER=/usr/local/bin/chromium MF_BROWSER_CONTAINER=1 MF_OSV_SCANNER=/usr/local/bin/osv-scanner
USER 10001
# The base image entrypoint writes to /root/.m2; not needed (maven.repo.local is set per run).
ENTRYPOINT []
CMD ["python", "-m", "mf.worker.main"]

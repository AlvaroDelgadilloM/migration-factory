# JDK 21 + Maven 3.9.11 (pinned) + Python 3.12 for the orchestration code. No Docker socket, non-root.
FROM maven:3.9.11-eclipse-temurin-21-noble
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv git util-linux \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/venv \
    && useradd --uid 10001 --create-home mf \
    && mkdir -p /artifacts /sources /work /m2 && chown 10001 /artifacts /work /m2
# Second JDK for migrations chosen with Java 25 (mf.versions): Temurin 25 LTS, pinned and checksum-verified. The default
# JDK (JAVA_HOME, from the base image) stays 21; the worker uses this one only to build and run Java 25 results.
ARG JDK25_URL=https://github.com/adoptium/temurin25-binaries/releases/download/jdk-25.0.4.1%2B1/OpenJDK25U-jdk_x64_linux_hotspot_25.0.4.1_1.tar.gz
ARG JDK25_SHA256=dbb698396d478e7fa2b1e50f4103324b2a99b90569ee27c33f2261f9215cf41e
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL -o /tmp/jdk25.tar.gz "$JDK25_URL" \
    && echo "$JDK25_SHA256  /tmp/jdk25.tar.gz" | sha256sum -c - \
    && mkdir -p /opt/jdk-25 && tar -xzf /tmp/jdk25.tar.gz -C /opt/jdk-25 --strip-components=1 && rm /tmp/jdk25.tar.gz \
    && /opt/jdk-25/bin/java -version
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
    MF_AIRFLOW_PYTHON=/opt/airflow-venv/bin/python MF_JDK25_HOME=/opt/jdk-25
USER 10001
# The base image entrypoint writes to /root/.m2; not needed (maven.repo.local is set per run).
ENTRYPOINT []
CMD ["python", "-m", "mf.worker.main"]

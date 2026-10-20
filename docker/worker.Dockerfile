FROM eclipse-temurin:25.0.4.1_1-jdk-noble AS jdk25
# JDK 21 + Maven 3.9.11 (pinned) + Python 3.12 for the orchestration code. No Docker socket, non-root.
FROM maven:3.9.11-eclipse-temurin-21-noble
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv git util-linux \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/venv \
    && useradd --uid 10001 --create-home mf \
    && mkdir -p /artifacts /sources /work /m2 && chown 10001 /artifacts /work /m2
# Second JDK for migrations chosen with Java 25 (mf.versions): the official Temurin 25.0.4.1 image, pinned and multi-arch
# (amd64 on the VPS, arm64 on Apple silicon). The default JDK (JAVA_HOME, from the base image) stays 21; the worker uses this
# one only to build and run Java 25 results.
COPY --from=jdk25 /opt/java/openjdk /opt/jdk-25
RUN /opt/jdk-25/bin/java -version
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

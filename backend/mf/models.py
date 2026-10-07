"""Persistence model (see docs/05_DATOS.md). Every business table has a UUID and created_at."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (JSON, BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text,
                        UniqueConstraint, Uuid)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

Json = JSON().with_variant(JSONB(), 'postgresql')
BigId = BigInteger().with_variant(Integer(), 'sqlite')  # sqlite only autoincrements INTEGER PKs


def now():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Entity:
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Organization(Entity, Base):
    __tablename__ = 'organization'
    name: Mapped[str] = mapped_column(String(200), unique=True)


class AppUser(Base):
    __tablename__ = 'app_user'
    id: Mapped[str] = mapped_column(String(255), primary_key=True)  # OIDC subject
    display_name: Mapped[str] = mapped_column(String(255), default='')
    email: Mapped[str | None] = mapped_column(String(320))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Repository(Entity, Base):
    """Project source. Only `git` has a URL/credential; `local` and `zip` never require Git."""
    __tablename__ = 'repository'
    source_type: Mapped[str] = mapped_column(String(10), default='git', server_default='git')  # git | local | zip
    provider: Mapped[str] = mapped_column(String(20))          # github | gitlab | generic | local | zip
    url: Mapped[str | None] = mapped_column(String(1000))
    credential_ref: Mapped[str | None] = mapped_column(String(300))  # env:NAME or vault:path, never the secret
    local_path: Mapped[str | None] = mapped_column(String(1000))     # folder on the worker host (local)
    upload_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)        # uploaded archive (zip)


class Upload(Entity, Base):
    """A validated ZIP waiting to be (or already) attached to a project. Owned by its uploader."""
    __tablename__ = 'upload'
    owner: Mapped[str] = mapped_column(String(255), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    object_key: Mapped[str] = mapped_column(String(600), unique=True)
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    structure: Mapped[dict] = mapped_column(Json, default=dict)  # root, modules, entries, uncompressedBytes
    project_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class Project(Entity, Base):
    __tablename__ = 'project'
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('organization.id'))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default='')
    repository_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('repository.id'))
    default_branch: Mapped[str] = mapped_column(String(255), default='main')
    target_runtime: Mapped[str | None] = mapped_column(String(20))
    required_gates: Mapped[list] = mapped_column(Json, default=lambda: ['G0', 'G1', 'G2', 'G3', 'G4', 'G5', 'G6'])
    independent_review: Mapped[bool] = mapped_column(Boolean, default=True)
    pr_config: Mapped[dict | None] = mapped_column(Json)       # {provider, repository, baseBranch, credentialRef}
    created_by: Mapped[str] = mapped_column(String(255))
    version: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (UniqueConstraint('organization_id', 'name'),)


class Membership(Base):
    __tablename__ = 'membership'
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('project.id', ondelete='CASCADE'), primary_key=True)
    subject_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    role: Mapped[str] = mapped_column(String(20))                 # owner | architect | developer | reviewer | auditor
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class TargetProfile(Entity, Base):
    __tablename__ = 'target_profile'
    key: Mapped[str] = mapped_column(String(100))
    version: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(200))
    runtime: Mapped[str] = mapped_column(String(20))              # spring | quarkus
    java_version: Mapped[str] = mapped_column(String(10))
    camel_version: Mapped[str] = mapped_column(String(30))
    runtime_version: Mapped[str] = mapped_column(String(30))
    bom_coordinates: Mapped[list] = mapped_column(Json)
    tooling: Mapped[dict] = mapped_column(Json)                   # pinned rewrite plugin + recipe artifacts
    dependency_mapping: Mapped[dict] = mapped_column(Json)
    sources: Mapped[list] = mapped_column(Json, default=list)
    status: Mapped[str] = mapped_column(String(20), default='draft')  # draft | validated | retired
    digest: Mapped[str] = mapped_column(String(64))
    __table_args__ = (UniqueConstraint('key', 'version'),)


class RuleCatalog(Entity, Base):
    __tablename__ = 'rule_catalog'
    version: Mapped[str] = mapped_column(String(50), unique=True)
    digest: Mapped[str] = mapped_column(String(64))
    source_uri: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), default='active')  # draft | active | retired
    rules: Mapped[list] = mapped_column(Json)


class Job(Entity, Base):
    __tablename__ = 'job'
    kind: Mapped[str] = mapped_column(String(30))       # scan | preview | apply | pull_request
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('project.id', ondelete='CASCADE'))
    subject_id: Mapped[uuid.UUID] = mapped_column(Uuid)  # scan or execution id
    state: Mapped[str] = mapped_column(String(20), default='queued')
    # queued | running | succeeded | failed | cancelled | timed_out
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=2)
    lease_owner: Mapped[str | None] = mapped_column(String(200))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    phase: Mapped[str | None] = mapped_column(String(50))
    progress: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(60))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(255))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index('ix_job_state_lease', 'state', 'lease_until'),)


class JobEvent(Base):
    __tablename__ = 'job_event'
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('job.id', ondelete='CASCADE'), index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    level: Mapped[str] = mapped_column(String(10), default='info')
    phase: Mapped[str | None] = mapped_column(String(50))
    message: Mapped[str] = mapped_column(Text)  # already redacted


class Scan(Entity, Base):
    __tablename__ = 'scan'
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('project.id', ondelete='CASCADE'))
    ref: Mapped[str] = mapped_column(String(255))
    commit_sha: Mapped[str | None] = mapped_column(String(64))       # git only
    snapshot_hash: Mapped[str | None] = mapped_column(String(64))    # content hash of the analyzed copy (all sources)
    snapshot_artifact_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    project_root: Mapped[str | None] = mapped_column(String(500))    # Maven root inside the source
    catalog_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('rule_catalog.id'))
    target: Mapped[str] = mapped_column(String(20), default='spring')
    status: Mapped[str] = mapped_column(String(20), default='queued')
    job_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    summary: Mapped[dict] = mapped_column(Json, default=dict)
    errors: Mapped[list] = mapped_column(Json, default=list)
    components: Mapped[list] = mapped_column(Json, default=list)
    route_builders: Mapped[list] = mapped_column(Json, default=list, server_default='[]')  # RouteBuilder classes (for runtime wiring/smoke tests)
    maven_resolution: Mapped[str] = mapped_column(String(30), default='pending')  # pending | maven-effective | failed | skipped
    maven_resolution_detail: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(255))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index('ix_scan_project_created', 'project_id', 'created_at'),)


class Module(Entity, Base):
    __tablename__ = 'module'
    scan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('scan.id', ondelete='CASCADE'), index=True)
    path: Mapped[str] = mapped_column(String(1000))
    group_id: Mapped[str | None] = mapped_column(String(255))
    artifact_id: Mapped[str] = mapped_column(String(255))
    version: Mapped[str | None] = mapped_column(String(100))
    packaging: Mapped[str] = mapped_column(String(30))
    java_observed: Mapped[str | None] = mapped_column(String(40))
    java_resolved: Mapped[str | None] = mapped_column(String(40))
    java_source: Mapped[str | None] = mapped_column(String(100))
    camel_versions: Mapped[list] = mapped_column(Json, default=list)
    resolution: Mapped[str] = mapped_column(String(30))
    unresolved: Mapped[list] = mapped_column(Json, default=list)
    dependencies: Mapped[list] = mapped_column(Json, default=list)
    profiles: Mapped[list] = mapped_column(Json, default=list)


class Route(Entity, Base):
    __tablename__ = 'route'
    scan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('scan.id', ondelete='CASCADE'), index=True)
    route_key: Mapped[str] = mapped_column(String(1100))
    route_id: Mapped[str | None] = mapped_column(String(255))
    dsl: Mapped[str] = mapped_column(String(10))
    file: Mapped[str] = mapped_column(String(1000))
    line: Mapped[int] = mapped_column(Integer)
    detection: Mapped[str] = mapped_column(String(40))
    from_uri: Mapped[str | None] = mapped_column(Text)
    dynamic_from: Mapped[bool] = mapped_column(Boolean, default=False)


class Endpoint(Entity, Base):
    __tablename__ = 'endpoint'
    route_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('route.id', ondelete='CASCADE'), index=True)
    component: Mapped[str | None] = mapped_column(String(100))
    redacted_uri: Mapped[str | None] = mapped_column(Text)
    dynamic: Mapped[bool] = mapped_column(Boolean, default=False)
    kind: Mapped[str] = mapped_column(String(30))
    line: Mapped[int] = mapped_column(Integer)


class Finding(Entity, Base):
    __tablename__ = 'finding'
    scan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('scan.id', ondelete='CASCADE'))
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('project.id', ondelete='CASCADE'), index=True)
    fingerprint: Mapped[str] = mapped_column(String(1200))
    rule_id: Mapped[str] = mapped_column(String(60))
    rule_version: Mapped[int] = mapped_column(Integer)
    file: Mapped[str] = mapped_column(String(1000))
    line: Mapped[int] = mapped_column(Integer)
    severity: Mapped[str] = mapped_column(String(10))
    classification: Mapped[str] = mapped_column(String(20))
    blocking: Mapped[bool] = mapped_column(Boolean, default=False)
    recommendation: Mapped[str] = mapped_column(Text)
    evidence: Mapped[str] = mapped_column(String(200), default='')
    status: Mapped[str] = mapped_column(String(20), default='open')  # open | proposed | resolved | discarded
    version: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (Index('ix_finding_scan_status_sev', 'scan_id', 'status', 'severity'),
                      UniqueConstraint('scan_id', 'fingerprint'))


class FindingReview(Entity, Base):
    __tablename__ = 'finding_review'
    finding_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('finding.id', ondelete='CASCADE'), index=True)
    reviewer: Mapped[str] = mapped_column(String(255))
    from_status: Mapped[str] = mapped_column(String(20))
    decision: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(Text)
    evidence_artifact_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class Plan(Entity, Base):
    __tablename__ = 'plan'
    scan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('scan.id', ondelete='CASCADE'))
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('project.id', ondelete='CASCADE'), index=True)
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('target_profile.id'))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default='draft')  # draft | approved | superseded
    digest: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(String(255))
    approved_by: Mapped[str | None] = mapped_column(String(255))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    row_version: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (UniqueConstraint('scan_id', 'version'),)


class PlanStep(Entity, Base):
    __tablename__ = 'plan_step'
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('plan.id', ondelete='CASCADE'), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    key: Mapped[str] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(20))            # recipe | manual | validation
    classification: Mapped[str] = mapped_column(String(20))
    gate: Mapped[str | None] = mapped_column(String(5))
    recipe: Mapped[dict | None] = mapped_column(Json)        # {activeRecipes, yaml, artifacts, pluginVersion}
    preconditions: Mapped[list] = mapped_column(Json, default=list)
    finding_ids: Mapped[list] = mapped_column(Json, default=list)
    depends_on: Mapped[list] = mapped_column(Json, default=list)
    rollback: Mapped[str] = mapped_column(Text, default='')
    notes: Mapped[list] = mapped_column(Json, default=list)


class Execution(Entity, Base):
    __tablename__ = 'execution'
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('plan.id', ondelete='CASCADE'), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('project.id', ondelete='CASCADE'), index=True)
    mode: Mapped[str] = mapped_column(String(10))            # preview | apply
    state: Mapped[str] = mapped_column(String(20), default='queued')
    job_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    base_sha: Mapped[str | None] = mapped_column(String(64))         # git commit of the source (git only)
    base_snapshot: Mapped[str | None] = mapped_column(String(64))    # snapshot content hash the candidate starts from
    candidate_sha: Mapped[str | None] = mapped_column(String(64))
    steps: Mapped[list] = mapped_column(Json, default=list)
    created_by: Mapped[str] = mapped_column(String(255))
    rolled_back: Mapped[bool] = mapped_column(Boolean, default=False)
    pr_url: Mapped[str | None] = mapped_column(String(1000))
    summary: Mapped[dict] = mapped_column(Json, default=dict)


class DiffFile(Entity, Base):
    __tablename__ = 'diff_file'
    execution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('execution.id', ondelete='CASCADE'), index=True)
    path: Mapped[str] = mapped_column(String(1000))
    change_type: Mapped[str] = mapped_column(String(10))      # added | modified | deleted | renamed
    additions: Mapped[int] = mapped_column(Integer, default=0)
    deletions: Mapped[int] = mapped_column(Integer, default=0)
    recipes: Mapped[list] = mapped_column(Json, default=list)
    patch: Mapped[str] = mapped_column(Text)                  # redacted unified diff for this file
    review_status: Mapped[str] = mapped_column(String(10), default='pending')  # pending | approved | rejected
    reviewed_by: Mapped[str | None] = mapped_column(String(255))
    review_reason: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1)


class Validation(Entity, Base):
    __tablename__ = 'validation'
    execution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('execution.id', ondelete='CASCADE'), index=True)
    suite: Mapped[str] = mapped_column(String(30))            # compile | test | secret-scan | equivalence | contracts
    target: Mapped[str] = mapped_column(String(10))           # baseline | candidate
    gate: Mapped[str | None] = mapped_column(String(5))
    status: Mapped[str] = mapped_column(String(10))           # PASS | FAIL | ERROR | SKIPPED | NOT_RUN
    exit_code: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    tests: Mapped[dict | None] = mapped_column(Json)
    detail: Mapped[str | None] = mapped_column(Text)
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    recorded_by: Mapped[str | None] = mapped_column(String(255))  # set for manual evidence


class Artifact(Entity, Base):
    __tablename__ = 'artifact'
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('project.id', ondelete='CASCADE'), index=True)
    scan_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    execution_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    kind: Mapped[str] = mapped_column(String(30))             # report-json | report-html | log | patch | bundle | effective-pom
    name: Mapped[str] = mapped_column(String(255))
    object_key: Mapped[str] = mapped_column(String(600), unique=True)
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    content_type: Mapped[str] = mapped_column(String(100))


class AuditEvent(Base):
    __tablename__ = 'audit_event'
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    actor: Mapped[str] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(80))
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str] = mapped_column(String(100))
    before_hash: Mapped[str | None] = mapped_column(String(64))
    after_hash: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict] = mapped_column(Json, default=dict)
    request_id: Mapped[str | None] = mapped_column(String(64))


class Outbox(Base):
    __tablename__ = 'outbox'
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(50))
    payload: Mapped[dict] = mapped_column(Json)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)


class IdempotencyRecord(Base):
    __tablename__ = 'idempotency_record'
    actor: Mapped[str] = mapped_column(String(255), primary_key=True)
    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    endpoint: Mapped[str] = mapped_column(String(300))
    request_hash: Mapped[str] = mapped_column(String(64))
    response_status: Mapped[int] = mapped_column(Integer)
    response_body: Mapped[dict] = mapped_column(Json)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class EffortLog(Entity, Base):
    """Real time spent on a unit of work; feeds the median calibration of the effort estimate."""
    __tablename__ = 'effort_log'
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('project.id', ondelete='CASCADE'), index=True)
    category: Mapped[str] = mapped_column(String(60), index=True)
    units: Mapped[int] = mapped_column(Integer, default=1)
    minutes: Mapped[int] = mapped_column(Integer)
    subject_type: Mapped[str] = mapped_column(String(20))      # finding | diff_file | integration | other
    subject_id: Mapped[str | None] = mapped_column(String(100))
    note: Mapped[str] = mapped_column(Text, default='')
    actor: Mapped[str] = mapped_column(String(255))


class Modernization(Entity, Base):
    """Modernization of the candidate of an apply execution (12-PROMPT-CLAUDE-MODERNIZATION); the candidate is never changed."""
    __tablename__ = 'modernization'
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('project.id', ondelete='CASCADE'), index=True)
    execution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('execution.id', ondelete='CASCADE'), index=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    state: Mapped[str] = mapped_column(String(20), default='queued')
    approved: Mapped[list] = mapped_column(Json, default=list)
    results: Mapped[list] = mapped_column(Json, default=list)
    quality_before: Mapped[dict] = mapped_column(Json, default=dict)
    quality_after: Mapped[dict] = mapped_column(Json, default=dict)
    summary: Mapped[dict] = mapped_column(Json, default=dict)
    created_by: Mapped[str] = mapped_column(String(255))


class WorkspaceMigration(Entity, Base):
    """Migration of every project of a MULTI_PROJECT_REPOSITORY by waves (18/19-CORRECCION) from a workspace scan."""
    __tablename__ = 'workspace_migration'
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('project.id', ondelete='CASCADE'), index=True)
    scan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('scan.id', ondelete='CASCADE'), index=True)
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('target_profile.id'))
    job_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    state: Mapped[str] = mapped_column(String(20), default='queued')
    java: Mapped[str] = mapped_column(String(5))
    accepted: Mapped[dict] = mapped_column(Json, default=dict)   # rule -> recorded reason (architect decision)
    approved: Mapped[list] = mapped_column(Json, default=list)
    results: Mapped[dict] = mapped_column(Json, default=dict)    # project -> {status, reason, dependency, artifacts}
    summary: Mapped[dict] = mapped_column(Json, default=dict)
    created_by: Mapped[str] = mapped_column(String(255))
    retry_of: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # doc 20 §20: previous run whose successful projects are reused
    retry: Mapped[list] = mapped_column(Json, default=list)     # projects to retry (empty + retry_of = resume every non-successful)

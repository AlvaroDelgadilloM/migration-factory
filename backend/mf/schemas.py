"""API contracts (camelCase JSON). OpenAPI at /api/v1/openapi.json is generated from these models."""
import uuid
from datetime import datetime
from typing import Annotated, Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

T = TypeVar('T')


class Dto(BaseModel):
    model_config = ConfigDict(from_attributes=True, alias_generator=to_camel, populate_by_name=True, serialize_by_alias=True)


class Page(Dto, Generic[T]):
    items: list[T]
    total: int
    next_cursor: str | None = None


Role = Literal['owner', 'architect', 'developer', 'reviewer', 'auditor']
GATE_IDS = ('G0', 'G1', 'G2', 'G3', 'G4', 'G5', 'G6')


class PrConfig(Dto):
    provider: Literal['github']
    repository: str = Field(pattern=r'^[\w.-]{1,100}/[\w.-]{1,100}$')
    base_branch: str = Field(default='main', pattern=r'^[\w./-]{1,200}$')
    credential_ref: str = Field(pattern=r'^(env:MF_CRED_[A-Z0-9_]{1,100}|vault:[\w./-]{1,200})$')


SOURCE_FIELDS = {'git': {'repository_url', 'default_branch', 'credential_ref'}, 'local': {'local_path'}, 'zip': {'upload_id'}}
REQUIRED_SOURCE_FIELDS = {'git': {'repository_url'}, 'local': {'local_path'}, 'zip': {'upload_id'}}


class ProjectCreate(Dto):
    name: str = Field(min_length=2, max_length=200, pattern=r'^[\w .\-()]+$')
    description: str = Field(default='', max_length=2000)
    source_type: Literal['local', 'zip', 'git']
    target_runtime: Literal['spring', 'quarkus']
    # git
    repository_url: str | None = Field(default=None, max_length=1000)
    default_branch: str | None = Field(default=None, pattern=r'^[\w./-]{1,200}$')
    credential_ref: str | None = None
    # local
    local_path: str | None = Field(default=None, max_length=1000)
    # zip
    upload_id: uuid.UUID | None = None

    @model_validator(mode='after')
    def _per_source(self):
        given = {f for f in ('repository_url', 'default_branch', 'credential_ref', 'local_path', 'upload_id') if getattr(self, f)}
        missing = REQUIRED_SOURCE_FIELDS[self.source_type] - given
        if missing:
            raise ValueError(f'Origen {self.source_type}: faltan {sorted(missing)}')
        extra = given - SOURCE_FIELDS[self.source_type]
        if extra:
            raise ValueError(f'Origen {self.source_type}: campos no aplicables {sorted(extra)}')
        if self.source_type == 'git' and not self.default_branch:
            self.default_branch = 'main'
        return self


class ProjectUpdate(Dto):
    description: str | None = Field(default=None, max_length=2000)
    target_runtime: Literal['spring', 'quarkus'] | None = None
    required_gates: list[str] | None = None
    independent_review: bool | None = None
    pr_config: PrConfig | None = None

    @field_validator('required_gates')
    @classmethod
    def _gates(cls, v):
        if v is not None and not set(v) <= set(GATE_IDS):
            raise ValueError('gate desconocido')
        return v


class ProjectOut(Dto):
    id: uuid.UUID
    name: str
    description: str
    source_type: str
    repository_url: str | None
    repository_provider: str
    credential_ref: str | None
    local_path: str | None = None
    upload: dict | None = None
    default_branch: str
    target_runtime: str | None
    required_gates: list[str]
    independent_review: bool
    pr_config: dict | None
    my_role: str | None
    version: int
    created_at: datetime
    last_scan: dict | None = None


class MemberIn(Dto):
    subject_id: str = Field(min_length=1, max_length=255)
    role: Role


class MemberOut(Dto):
    subject_id: str
    role: str
    display_name: str | None = None


class ScanCreate(Dto):
    ref: str | None = Field(default=None, pattern=r'^[\w./-]{1,200}$')
    catalog_id: uuid.UUID | None = None
    target: Literal['spring', 'quarkus'] = 'spring'
    # doc 17: one project of a multi-project repository (its folder, as listed in the workspace of a previous scan)
    project_root: str | None = Field(default=None, pattern=r'^[\w.\-]+(/[\w.\-]+)*$', max_length=500)

    @field_validator('project_root')
    @classmethod
    def _no_dotdot(cls, v):
        if v and any(part in ('.', '..') for part in v.split('/')):
            raise ValueError('ruta de proyecto no válida')
        return v


class JobOut(Dto):
    id: uuid.UUID
    kind: str
    state: str
    phase: str | None
    progress: int
    attempts: int
    cancel_requested: bool
    error_code: str | None
    error_message: str | None
    created_by: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class ScanOut(Dto):
    job: 'JobOut | None' = None
    id: uuid.UUID
    project_id: uuid.UUID
    ref: str
    commit_sha: str | None
    snapshot_hash: str | None = None
    project_root: str | None = None
    catalog_id: uuid.UUID
    target: str
    status: str
    job_id: uuid.UUID | None
    summary: dict
    errors: list
    components: list
    route_builders: list = []
    maven_resolution: str
    maven_resolution_detail: str | None
    created_by: str
    created_at: datetime
    finished_at: datetime | None


class ModuleOut(Dto):
    id: uuid.UUID
    path: str
    group_id: str | None
    artifact_id: str
    version: str | None
    packaging: str
    java_observed: str | None
    java_resolved: str | None
    java_source: str | None
    camel_versions: list
    resolution: str
    unresolved: list
    dependencies: list
    profiles: list


class EndpointOut(Dto):
    component: str | None
    redacted_uri: str | None
    dynamic: bool
    kind: str
    line: int


class RouteOut(Dto):
    id: uuid.UUID
    route_key: str
    route_id: str | None
    dsl: str
    file: str
    line: int
    detection: str
    from_uri: str | None
    dynamic_from: bool
    endpoints: list[EndpointOut] = []


class FindingOut(Dto):
    id: uuid.UUID
    scan_id: uuid.UUID
    fingerprint: str
    rule_id: str
    rule_version: int
    file: str
    line: int
    severity: str
    classification: str
    blocking: bool
    recommendation: str
    evidence: str
    status: str
    version: int


class FindingReviewIn(Dto):
    decision: Literal['open', 'proposed', 'resolved', 'discarded']
    reason: str = Field(min_length=10, max_length=4000)
    minutes: int | None = Field(default=None, ge=1, le=60 * 24 * 10)  # real time spent (effort calibration)


class FindingReviewOut(Dto):
    id: uuid.UUID
    reviewer: str
    from_status: str
    decision: str
    reason: str
    created_at: datetime


class FindingDetail(FindingOut):
    reviews: list['FindingReviewOut'] = []


class PlanCreate(Dto):
    profile_id: uuid.UUID


class PlanOut(Dto):
    id: uuid.UUID
    scan_id: uuid.UUID
    project_id: uuid.UUID
    profile_id: uuid.UUID
    version: int
    status: str
    digest: str
    created_by: str
    approved_by: str | None
    approved_at: datetime | None
    created_at: datetime
    row_version: int
    steps: list[dict] = []
    profile: dict | None = None


class ExecutionCreate(Dto):
    mode: Literal['preview', 'apply']


class ExecutionOut(Dto):
    id: uuid.UUID
    plan_id: uuid.UUID
    project_id: uuid.UUID
    mode: str
    state: str
    job_id: uuid.UUID | None
    base_sha: str | None
    base_snapshot: str | None = None
    candidate_sha: str | None
    steps: list
    created_by: str
    rolled_back: bool
    pr_url: str | None
    summary: dict
    created_at: datetime
    job: JobOut | None = None
    gates: list[dict] = []
    pr_blockers: list[str] = []


class DiffFileOut(Dto):
    id: uuid.UUID
    path: str
    change_type: str
    additions: int
    deletions: int
    recipes: list
    review_status: str
    reviewed_by: str | None
    review_reason: str | None
    version: int


class DiffFileDetail(DiffFileOut):
    patch: str


class DiffReviewIn(Dto):
    decision: Literal['approved', 'rejected']
    reason: str = Field(default='', max_length=4000)
    minutes: int | None = Field(default=None, ge=1, le=60 * 24 * 10)


class EvidenceIn(Dto):
    suite: Literal['equivalence', 'contracts']
    status: Literal['PASS', 'FAIL', 'SKIPPED']
    detail: str = Field(min_length=10, max_length=4000)


class ValidationOut(Dto):
    id: uuid.UUID
    suite: str
    target: str
    gate: str | None
    status: str
    exit_code: int | None
    duration_ms: int | None
    tests: dict | None
    detail: str | None
    artifact_id: uuid.UUID | None
    recorded_by: str | None
    created_at: datetime


class ArtifactOut(Dto):
    id: uuid.UUID
    kind: str
    name: str
    sha256: str
    size_bytes: int
    content_type: str
    scan_id: uuid.UUID | None
    execution_id: uuid.UUID | None
    created_at: datetime


class ProfileOut(Dto):
    id: uuid.UUID
    key: str
    version: int
    name: str
    runtime: str
    java_version: str
    camel_version: str
    runtime_version: str
    bom_coordinates: list
    tooling: dict
    dependency_mapping: dict
    sources: list
    status: str
    digest: str
    created_at: datetime


class ProfileCreate(Dto):
    key: str = Field(pattern=r'^[a-z0-9.\-]{3,100}$')
    name: str = Field(min_length=3, max_length=200)
    runtime: Literal['spring', 'quarkus']
    java_version: Literal['17', '21']
    camel_version: str = Field(pattern=r'^4\.\d+\.\d+$')
    runtime_version: str = Field(pattern=r'^\d+\.\d+\.\d+$')
    bom_coordinates: list[str] = Field(min_length=1)
    tooling: dict
    dependency_mapping: dict
    sources: list[str] = []

    @field_validator('bom_coordinates')
    @classmethod
    def _coords(cls, v):
        import re
        for c in v:
            if not re.fullmatch(r'[\w.\-]+:[\w.\-]+:\d[\w.\-]*', c) or c.endswith((':LATEST', ':RELEASE')):
                raise ValueError(f'coordenada inválida o no fijada: {c}')
        return v


class CatalogOut(Dto):
    id: uuid.UUID
    version: str
    digest: str
    source_uri: str
    status: str
    rules: list
    created_at: datetime


class CatalogCreate(Dto):
    catalog_version: str = Field(pattern=r'^[\w.\-]{1,50}$')
    rules: list[dict[str, Any]] = Field(min_length=1)


class AuditOut(Dto):
    id: int
    at: datetime
    project_id: uuid.UUID | None
    actor: str
    action: str
    entity_type: str
    entity_id: str
    before_hash: str | None
    after_hash: str | None
    details: dict
    request_id: str | None


class Me(Dto):
    subject: str
    name: str
    is_admin: bool
    auth_mode: str


class ScanAccepted(Dto):
    scan_id: uuid.UUID
    job_id: uuid.UUID
    status: str


class ExecutionAccepted(Dto):
    execution_id: uuid.UUID
    job_id: uuid.UUID
    status: str


class LogEvent(Dto):
    id: int
    at: str
    level: str
    phase: str | None
    message: str


class Permissions(Dto):
    role: str | None
    is_admin: bool
    permissions: list[str]


class AuthConfig(Dto):
    mode: str
    issuer: str | None
    client_id: str | None
    dev_users: list[dict]


class UploadOut(Dto):
    id: uuid.UUID
    filename: str
    sha256: str
    size_bytes: int
    structure: dict
    created_at: datetime


class Capabilities(Dto):
    local_source_enabled: bool
    local_source_roots: list[str]
    max_upload_mb: int
    git_allowed_hosts: list[str]


class BrowseEntry(Dto):
    name: str
    path: str
    has_pom: bool


class BrowseOut(Dto):
    path: str | None          # None = list of authorized roots
    parent: str | None        # None at a root (cannot go above it)
    has_pom: bool
    entries: list[BrowseEntry]
    truncated: bool = False


class EffortIn(Dto):
    category: str = Field(pattern=r'^[a-z0-9:\-/_A-Z]{2,60}$')
    minutes: int = Field(ge=1, le=60 * 24 * 10)
    units: int = Field(default=1, ge=1, le=1000)
    subject_type: Literal['finding', 'diff_file', 'integration', 'other'] = 'other'
    subject_id: str | None = Field(default=None, max_length=100)
    note: str = Field(default='', max_length=2000)


class EffortOut(Dto):
    id: uuid.UUID
    category: str
    minutes: int
    units: int
    subject_type: str
    subject_id: str | None
    note: str
    actor: str
    created_at: datetime


ScanOut.model_rebuild()
FindingDetail.model_rebuild()


class ModernizationIn(Dto):
    approve: list[Annotated[str, Field(pattern=r'^MOD_[A-Z_]{2,40}$')]] = Field(default_factory=list, max_length=20)


class ModernizationAccepted(Dto):
    modernization_id: uuid.UUID
    job_id: uuid.UUID
    status: str


class ModernizationOut(Dto):
    id: uuid.UUID
    project_id: uuid.UUID
    execution_id: uuid.UUID
    job_id: uuid.UUID | None
    state: str
    approved: list[str]
    results: list[dict]
    quality_before: dict
    quality_after: dict
    summary: dict
    created_by: str
    created_at: datetime
    job: JobOut | None = None


class WorkspaceMigrationIn(Dto):
    profile_id: uuid.UUID
    java: Literal['17', '21'] = '21'
    # architect decisions on blocking findings, applied to every project of the workspace (recorded in the audit)
    accept: dict[Annotated[str, Field(pattern=r'^[A-Z0-9_]{2,60}$')], Annotated[str, Field(min_length=10, max_length=500)]] = Field(default_factory=dict)
    approve: list[Annotated[str, Field(pattern=r'^[a-z0-9-]{2,60}$')]] = Field(default_factory=list, max_length=30)


class WorkspaceMigrationOut(Dto):
    id: uuid.UUID
    retry_of: uuid.UUID | None = None
    retry: list[str] = []
    project_id: uuid.UUID
    scan_id: uuid.UUID
    profile_id: uuid.UUID
    job_id: uuid.UUID | None
    state: str
    java: str
    accepted: dict
    approved: list[str]
    results: dict
    summary: dict
    created_by: str
    created_at: datetime
    job: JobOut | None = None


class WorkspaceRetryIn(Dto):
    projects: list[Annotated[str, Field(pattern=r'^[\w.\-/]{1,200}$')]] = Field(default_factory=list, max_length=100)

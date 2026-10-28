"""API contracts (camelCase JSON). OpenAPI at /api/v1/openapi.json is generated from these models."""
import json
import re
import uuid
from datetime import datetime
from typing import Annotated, Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

from .security import CREDENTIAL_REF

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
    provider: Literal['github', 'gitlab', 'bitbucket']
    # owner/name (GitHub), group[/subgroup...]/project (GitLab), workspace/repo-slug (Bitbucket Cloud)
    repository: str = Field(max_length=400, pattern=r'^[\w.-]+(/[\w.-]+){1,5}$')
    base_branch: str = Field(default='main', pattern=r'^[\w./-]{1,200}$')
    credential_ref: str = Field(max_length=400)

    @field_validator('credential_ref')
    @classmethod
    def _ref(cls, v):  # same rule as every credential reference (env:MF_CRED_* or vault:mount/path[#field])
        if not CREDENTIAL_REF.match(v) or (v.startswith('vault:') and '..' in v.split('#')[0].split('/')):
            raise ValueError('Use env:MF_CRED_NOMBRE o vault:montaje/ruta[#campo]; nunca el secreto')
        return v


SOURCE_FIELDS = {'git': {'repository_url', 'default_branch', 'credential_ref'}, 'local': {'local_path'}, 'zip': {'upload_id'}, 'agent': set()}
REQUIRED_SOURCE_FIELDS = {'git': {'repository_url'}, 'local': {'local_path'}, 'zip': {'upload_id'}, 'agent': set()}


class ProjectCreate(Dto):
    name: str = Field(min_length=2, max_length=200, pattern=r'^[\w .\-()]+$')
    description: str = Field(default='', max_length=2000)
    source_type: Literal['local', 'zip', 'git', 'agent']  # agent: the code stays on the user's machine (mf.api.agent)
    migration_type: Literal['JAVA_CAMEL', 'DATASTAGE_AIRFLOW', 'ODOO'] = 'JAVA_CAMEL'
    target_runtime: Literal['spring', 'quarkus', 'airflow', 'odoo19']
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
        expected = {'DATASTAGE_AIRFLOW': ('airflow',), 'ODOO': ('odoo19',)}.get(self.migration_type, ('spring', 'quarkus'))
        if self.target_runtime not in expected:
            raise ValueError('DATASTAGE_AIRFLOW va con target airflow; ODOO con odoo19; JAVA_CAMEL con spring o quarkus')
        if self.migration_type == 'ODOO' and self.source_type != 'zip':
            raise ValueError('ODOO: suba el respaldo de la base (ZIP con el dump)')
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
    migration_type: str = 'JAVA_CAMEL'
    required_gates: list[str]
    independent_review: bool
    pr_config: dict | None
    my_role: str | None
    version: int
    created_at: datetime
    last_scan: dict | None = None
    last_data_stage: dict | None = None


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
    java: Literal['21', '25'] = '21'  # only validated combinations with the profile (mf.versions)
    spring: str | None = Field(default=None, max_length=120)


class PlanOut(Dto):
    id: uuid.UUID
    scan_id: uuid.UUID
    project_id: uuid.UUID
    profile_id: uuid.UUID
    java_version: str | None = None
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
    artifact_id: uuid.UUID | None = None  # an evidence file uploaded to this execution (POST /executions/{id}/evidence-files)


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
    datastage_ai: bool = False  # doc 58: AI proposals for untranslated DataStage expressions can be requested


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


class UploadReplace(Dto):
    upload_id: uuid.UUID


class WorkspaceMigrationIn(Dto):
    # target 'profile' = Spring Boot / Quarkus by waves (profile_id required); 'tomcat-war' = Fuse/Karaf bundles -> one WAR (docs/19)
    target: Literal['profile', 'tomcat-war'] = 'profile'
    profile_id: uuid.UUID | None = None
    # Java and Spring line of the result: only validated combinations of the target (mf.versions, GET /target-versions)
    java: Literal['21', '25'] = '21'
    spring: str | None = Field(default=None, max_length=120)
    # architect decisions on blocking findings, applied to every project of the workspace (recorded in the audit)
    accept: dict[Annotated[str, Field(pattern=r'^[A-Z0-9_]{2,60}$')], Annotated[str, Field(min_length=10, max_length=500)]] = Field(default_factory=dict)
    approve: list[Annotated[str, Field(pattern=r'^[a-z0-9-]{2,60}$')]] = Field(default_factory=list, max_length=30)

    # tomcat-war client decisions (groupId, version, containers, patches, extraWarDependencies). `overlay` names a folder of the
    # machine that runs the CLI: never accepted from the platform
    config: dict | None = None
    verify_routes: bool = True

    @model_validator(mode='after')
    def _profile_or_tomcat(self):
        if self.target == 'profile' and self.profile_id is None:
            raise ValueError('profileId es obligatorio salvo con target tomcat-war')
        if self.target == 'tomcat-war':  # a profile target is checked by the API, which has its TargetProfile
            from . import versions
            versions.check(versions.tomcat_war(), self.java, self.spring)
        if self.config is not None:
            if self.target != 'tomcat-war':
                raise ValueError('config solo aplica a target tomcat-war')
            extra = set(self.config) - {'groupId', 'version', 'containers', 'patches', 'extraWarDependencies'}
            if extra:
                raise ValueError(f"config: claves no permitidas desde la plataforma: {', '.join(sorted(extra))}")
            if len(json.dumps(self.config)) > 200_000:
                raise ValueError('config demasiado grande (máx. 200 KB)')
            from .tomcat_war.pipeline import InvalidInput, validate_config
            try:
                validate_config(self.config)
            except (InvalidInput, re.error) as e:
                raise ValueError(f'config inválida: {e}') from None
        return self


class WorkspaceMigrationOut(Dto):
    id: uuid.UUID
    retry_of: uuid.UUID | None = None
    retry: list[str] = []
    project_id: uuid.UUID
    scan_id: uuid.UUID
    profile_id: uuid.UUID | None = None
    target: str = 'profile'
    config: dict | None = None
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


# --- Pruebas (manual and automatic HTTP tests; docs/19 «Pruebas») ---------------------------------------------------------
HttpMethod = Literal['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS']


class HttpTarget(Dto):
    label: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=8, max_length=500)  # base URL; validated against MF_TEST_ALLOWED_HOSTS


class HttpCase(Dto):
    """One request. Same fields as the cases file of `migrate.py verify responses`."""
    name: str = Field(min_length=1, max_length=200)
    method: HttpMethod = 'GET'
    path: str = Field(pattern=r'^/[^\s]*$', max_length=2000)
    headers: dict[Annotated[str, Field(pattern=r'^[A-Za-z0-9!#$%&\'*+.^_`|~-]{1,100}$')], Annotated[str, Field(max_length=4000)]] = \
        Field(default_factory=dict, max_length=40)
    body: str | None = Field(default=None, max_length=1_000_000)
    content_type: str | None = Field(default=None, max_length=200)
    accept: str | None = Field(default=None, min_length=10, max_length=500)  # reviewed difference: recorded with its reason

    @field_validator('headers')
    @classmethod
    def no_line_breaks(cls, v):
        if any(c in x for x in v.values() for c in '\r\n\0'):
            raise ValueError('un header no puede contener saltos de línea')
        return v


class PerformanceIn(Dto):
    """Comparative load test: the same concurrency and duration against the original, then against the migrated system."""
    users: int = Field(default=10, ge=1, le=100)
    seconds: int = Field(default=60, ge=10, le=900)


class RuntimeVariable(Dto):
    """A value for one ${NAME} of the migrated app: a plain value (never for a secret) or a credential reference."""
    value: str | None = Field(default=None, max_length=2000)
    ref: str | None = Field(default=None, max_length=400)

    @model_validator(mode='after')
    def one(self):
        if (self.value is None) == (self.ref is None):
            raise ValueError('indique value o ref')
        if self.ref is not None and not CREDENTIAL_REF.match(self.ref):
            raise ValueError('ref: env:MF_CRED_NOMBRE o vault:montaje/ruta[#campo]')
        return self


class HttpTestRunIn(Dto):
    """source 'migration' (default): the worker starts the original code and the migration's WAR and reads the cases from
    the code; only workspace_migration_id is required (name, container and data package are optional). source 'urls': two
    systems already running (original, migrated) and the cases to send."""
    source: Literal['migration', 'urls', 'execution'] = 'migration'
    name: str | None = Field(default=None, min_length=1, max_length=200)
    workspace_migration_id: uuid.UUID | None = None
    # source execution: an applied execution (Spring Boot / Tomcat WAR / Quarkus) the worker builds and starts; original optional
    execution_id: uuid.UUID | None = None
    variables: dict[Annotated[str, Field(pattern=r'^[A-Z][A-Z0-9_]{1,100}$')], RuntimeVariable] = Field(default_factory=dict, max_length=200)
    params: dict[Annotated[str, Field(pattern=r'^\w{1,60}$')], Annotated[str, Field(max_length=200, pattern=r'^[\w.\-~%]+$')]] = \
        Field(default_factory=dict, max_length=50)
    container: str | None = Field(default=None, pattern=r'^[\w.\-]{1,60}$')
    data_package_id: uuid.UUID | None = None  # POST /projects/{id}/http-tests/data-packages; none = smoke test
    original: HttpTarget | None = None
    migrated: HttpTarget | None = None
    cases: list[HttpCase] = Field(default_factory=list, max_length=500)  # source migration: extra cases besides the generated ones
    screenshots: bool = True
    performance: PerformanceIn | None = None  # source migration: also the comparative load test (mf.tomcat_war.perf)

    @model_validator(mode='after')
    def by_source(self):
        if self.source == 'migration' and not self.workspace_migration_id:
            raise ValueError('workspaceMigrationId es obligatorio para probar una migración')
        if self.source == 'execution' and not self.execution_id:
            raise ValueError('executionId es obligatorio para probar un proyecto migrado')
        if self.source == 'urls' and not (self.original and self.migrated and self.cases and self.name):
            raise ValueError('Con sistemas ya levantados indique name, original, migrated y al menos un caso')
        if self.source != 'migration' and self.performance:
            raise ValueError('La prueba de rendimiento levanta los dos sistemas: solo está disponible al probar una migración')
        return self


class HttpTestRunOut(Dto):
    id: uuid.UUID
    project_id: uuid.UUID
    job_id: uuid.UUID | None
    state: str
    name: str
    source: str = 'urls'
    workspace_migration_id: uuid.UUID | None = None
    execution_id: uuid.UUID | None = None
    variables: dict = {}
    params: dict = {}
    container: str | None = None
    data_artifact_id: uuid.UUID | None = None
    original: dict | None = None
    migrated: dict | None = None
    cases: list[dict]
    screenshots: bool
    performance: dict | None = None
    summary: dict
    created_by: str
    created_at: datetime
    job: JobOut | None = None


class ManualTestIn(Dto):
    request: HttpCase
    targets: list[HttpTarget] = Field(min_length=1, max_length=2)


CRON = r'^(@(hourly|daily|weekly|monthly|yearly)|[0-9*,/-]+( [0-9*,/-]+){3} [0-9A-Za-z*,/-]+)$'


class DataStageMigrationIn(Dto):
    timezone: str = Field(default='America/Mexico_City', pattern=r'^[A-Za-z_]+(/[A-Za-z_+-]+){0,2}$')
    ai_assist: bool = False  # doc 58: ask the configured LLM for proposals (never applied); refused when not configured
    # DataStage job/sequence name -> cron of its DAG; None = keep the project's saved schedules (the DSX has none)
    schedules: dict[Annotated[str, Field(min_length=1, max_length=255)], Annotated[str, Field(pattern=CRON)]] | None = Field(default=None, max_length=500)


class DataStageSchedulesIn(Dto):
    schedules: dict[Annotated[str, Field(min_length=1, max_length=255)], Annotated[str, Field(pattern=CRON)]] = Field(max_length=500)


class DataStageMigrationOut(Dto):
    id: uuid.UUID
    project_id: uuid.UUID
    job_id: uuid.UUID | None
    state: str
    options: dict
    summary: dict
    results: dict
    action_state: dict = {}
    reconciliation: dict = {}
    created_by: str
    created_at: datetime
    job: JobOut | None = None


class DataStageActionIn(Dto):
    key: str = Field(pattern=r'^[0-9a-f]{16}$')
    done: bool
    note: str | None = Field(default=None, max_length=1000)


class HttpTestSource(Dto):
    """A tomcat-war migration that can be tested automatically (its WAR and the analysed source are stored)."""
    workspace_migration_id: uuid.UUID
    scan_id: uuid.UUID
    created_at: datetime
    state: str
    build: str | None
    containers: list[str]
    war: bool


class DataPackageOut(Dto):
    id: uuid.UUID
    name: str
    size_bytes: int
    contents: dict  # etc files, sql scripts, mocks, values, extra cases


# --- target versions (mf.versions) -------------------------------------------------------------------------------------
class JavaChoice(Dto):
    version: str
    validated: bool
    reason: str | None = None


class SpringChoice(Dto):
    key: str
    label: str
    java: list[str]  # Java versions this Spring line is validated with


class TargetVersions(Dto):
    target: Literal['tomcat-war', 'profile']
    profile_id: str | None = None
    runtime: Literal['tomcat', 'spring', 'quarkus']
    label: str
    java: list[JavaChoice]
    spring: list[SpringChoice]

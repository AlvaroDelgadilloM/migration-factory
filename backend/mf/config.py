"""Runtime settings from environment variables. Secrets are never defaulted to real values."""
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='MF_', env_file=None, extra='ignore')

    env: str = 'development'                      # development | test | production
    database_url: str = 'sqlite:///./mf-dev.db'
    redis_url: str = 'redis://localhost:6379/0'

    # Authentication: 'oidc' validates bearer tokens against the issuer JWKS.
    # 'dev' issues HS256 tokens for fixture users and is refused when env=production.
    auth_mode: str = 'dev'
    oidc_issuer: str = ''
    oidc_audience: str = ''
    oidc_jwks_url: str = ''                       # optional override (e.g. internal URL of the IdP)
    oidc_admin_role: str = 'mf-admin'
    oidc_client_id: str = 'migration-factory-ui'  # exposed to the UI, not a secret
    dev_jwt_secret: str = ''

    artifact_dir: Path = Path('/tmp/mf-artifacts')
    work_dir: Path = Path('/tmp/mf-work')
    rules_catalog: Path = REPO_ROOT / 'rules' / 'catalog.json'

    # Git access policy (SSRF protection)
    git_allowed_hosts: str = 'github.com,gitlab.com,bitbucket.org'
    git_internal_hosts: str = ''                  # hosts allowed to resolve to private IPs (corporate Git)
    # Local folders: comma-separated directories *on the worker/API host* that may be registered as sources.
    # Empty = local folders disabled (remote platform: use ZIP or a local agent).
    local_source_roots: str = ''
    # ZIP uploads
    max_upload_mb: int = 200
    zip_max_entries: int = 20000
    zip_max_uncompressed_mb: int = 1024
    zip_max_ratio: int = 200                      # per-entry compression ratio (zip bomb guard)

    # Worker limits
    job_timeout_seconds: int = 1800
    workspace_timeout_seconds: int = 4 * 3600  # a workspace migration runs every project of every wave
    step_timeout_seconds: int = 900
    lease_seconds: int = 60
    max_attempts: int = 2
    max_repo_mb: int = 500
    maven_mirror_url: str = 'https://repo1.maven.org/maven2'
    # Additional APPROVED repositories, "id|https-url" comma-separated (e.g. Shibboleth for opensaml used by CXF WS-Security).
    maven_extra_repos: str = ''
    # improvements/06 network mode: offline (local repository only, mvn -o) | central (approved mirror only) |
    # allowlist (mirror + MF_MAVEN_EXTRA_REPOS)
    maven_network: Literal['offline', 'central', 'allowlist'] = 'allowlist'
    maven_repo_local: Path = Path('/tmp/mf-m2')
    maven_file_locks: bool = False  # true when several workers share maven_repo_local
    # doc 32: which baseline blocks still allow a DEGRADED migration (code failures need --allow-baseline-failure)
    baseline_allow_external_dependency_failure: bool = True
    baseline_allow_repository_failure: bool = False
    baseline_allow_network_failure: bool = False
    baseline_allow_auth_failure: bool = False
    baseline_allow_internal_artifact_failure: bool = False
    maven_opts: str = '-Xmx768m'
    # Fixed PATH for build subprocesses (never inherited). The CLI sets it to where mvn/git/java live locally.
    tool_path: str = '/opt/maven/bin:/opt/java/openjdk/bin:/usr/local/bin:/usr/bin:/bin'

    cors_origins: str = 'http://localhost:4200'

    @field_validator('auth_mode')
    @classmethod
    def _mode(cls, v):
        if v not in ('oidc', 'dev'):
            raise ValueError('MF_AUTH_MODE must be oidc or dev')
        return v

    def list_of(self, value: str):
        return [x.strip().lower() for x in value.split(',') if x.strip()]


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if s.env == 'production' and s.auth_mode == 'dev':
        raise RuntimeError('MF_AUTH_MODE=dev is not allowed in production')
    if s.auth_mode == 'dev' and len(s.dev_jwt_secret) < 32:
        raise RuntimeError('MF_DEV_JWT_SECRET must be set (>=32 chars) in dev auth mode')
    if s.auth_mode == 'oidc' and not (s.oidc_issuer and s.oidc_audience):
        raise RuntimeError('MF_OIDC_ISSUER and MF_OIDC_AUDIENCE are required in oidc mode')
    return s

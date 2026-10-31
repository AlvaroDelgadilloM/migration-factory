import { components } from './api-types';

type S = components['schemas'];
export type Project = S['ProjectOut'];
export type Scan = S['ScanOut'];
export type Job = S['JobOut'];
export type Finding = S['FindingOut'];
export type FindingDetail = S['FindingDetail'];
export type Module = S['ModuleOut'];
export type Route = S['RouteOut'];
export type Plan = S['PlanOut'];
export type Execution = S['ExecutionOut'];
export type DiffFile = S['DiffFileOut'];
export type DiffFileDetail = S['DiffFileDetail'];
export type Validation = S['ValidationOut'];
export type Artifact = S['ArtifactOut'];
export type Profile = S['ProfileOut'];
export type Catalog = S['CatalogOut'];
export type AuditEvent = S['AuditOut'];
export type LogEvent = S['LogEvent'];
export type Permissions = S['Permissions'];
export type AuthConfig = S['AuthConfig'];
export type Member = S['MemberOut'];
export type Me = S['Me'];
export type Upload = S['UploadOut'];
export type Capabilities = S['Capabilities'];
export type Browse = S['BrowseOut'];
export interface EffortRow { category: string; label: string; unit: string; units: number; findings: number;
  hoursPerUnit: number | null; source: string; subtotalHours: number | null; detail: string[] }
export type Modernization = S['ModernizationOut'];
export type DataStageMigration = S['DataStageMigrationOut'];
export type WorkspaceMigration = S['WorkspaceMigrationOut'];
export interface ExecutionResult {
  executionId: string; sourceType: 'LOCAL' | 'GIT_REMOTE'; status: string;
  download: { label: string; kind: 'project' | 'diagnostic' | 'analysis'; warning: string | null; available: boolean };
  artifacts: { candidateZip?: string; report?: string; reportJson?: string; diff?: string; manualActions?: string; camelComponents?: string };
  pullRequestAvailable: boolean; pullRequest: { available: boolean; shown: boolean; reasons: string[] };
}
export interface ChangeLine { file: string; lines: string; before: string; after: string }
export interface ChangeEntry { step: string; kind: string | null; rules: string[]; confidence: string; confidenceMeaning: string; rationale: string[];
  snapshotBefore: string | null; snapshotAfter: string | null; files: string[]; patch: string; patchArtifactId?: string; patchSha256: string;
  rollback: { available: boolean; command: string; note?: string }; changes: ChangeLine[] }
export interface ModernizationRule { id: string; tier: 'SAFE' | 'REFACTOR' | 'ARCHITECTURE'; level: string; publicContract: boolean; mechanism: string; title: string; rationale: string }
export interface EffortEstimate { rows: EffortRow[]; knownHours: number; uncalibrated: string[]; complete: boolean; note: string; diffBasis: string; configVersion: string }
export type SourceType = 'local' | 'zip' | 'git' | 'agent';

export interface Page<T> { items: T[]; total: number; nextCursor?: string | null }

export interface ApiErrorBody { code: string; message: string; requestId: string | null; details: Record<string, unknown> }

/** Plan steps and gates are evaluated server-side; these shapes mirror mf/planning.py and mf/gates.py. */
export interface Precondition { code: string; description: string; ok: boolean; detail: string }
export interface PlanStep {
  id: string; ordinal: number; key: string; title: string; kind: 'recipe' | 'transform' | 'manual' | 'validation';
  classification: string; gate: string | null; state: 'ready' | 'blocked' | 'cleared' | 'pending';
  blockedReasons: string[]; preconditions: Precondition[]; findingIds: string[]; dependsOn: string[];
  recipe: { activeRecipes?: string[]; yaml?: string; edits?: { op: string; file: string; line: number; old?: string; new?: string }[] } | null; rollback: string; notes: string[];
}
export interface ExecutionStep {
  key: string; title: string; state: string; reasons?: string[]; commit?: string | null; recipes?: string[];
  logArtifactId?: string; durationMs?: number; exitCode?: number; files?: string[];
}
export interface Gate { gate: string; title: string; status: string; detail: string; required: boolean }

// Pruebas (manual and automatic HTTP tests)
export type HttpTestRun = S['HttpTestRunOut'];
export type HttpCase = S['HttpCase'];
export type HttpTestSource = S['HttpTestSource'];
export type DataPackage = Omit<S['DataPackageOut'], 'contents'> & {
  contents: { etc: string[]; sql: string[]; mocks: number; values: number; cases: number } };
export interface HttpAnswer { status: number; type: string; body: string; truncated: boolean }
export interface HttpCaseResult { name: string; method: string; path: string; state: string; kind: 'ERROR_TEXT' | 'DATA' | null; issues: string[];
  accept: string | null; request: { headers: Record<string, string>; body: string | null }; original: HttpAnswer; migrated: HttpAnswer }
export interface HttpRunSummary { status?: string; totals?: { cases: number; same: number; different: number; accepted: number; notExecutable: number };
  differencesByKind?: Record<string, number>; screenshots?: number; screenshotProblem?: string | null; cases?: HttpCaseResult[];
  artifacts?: { evidence: string; responses: string; performance?: string; jmeterPlan?: string };
  // automatic test from a migration (mf.tomcat_war.autotest)
  mode?: 'HUMO' | 'DATOS' | 'SMOKE'; container?: string; wave?: string | null; internalModules?: string[]; reason?: string;
  // automatic test of a migrated project (mf.runtime_test)
  runtime?: string; basePath?: string; endpoints?: { route: string; method: string; path: string }[]; variables?: string[]; modules?: string[]; generatedCases?: number; externalCalls?: string[];
  startup?: Record<'original' | 'migrado', { started: number | null; total: number | null; failed: string[] }>;
  configuration?: { files: number; replaced: number; generated: number; fromDataPackage: string[] };
  // comparative load test (mf.tomcat_war.perf), when requested
  performance?: { users: number; seconds: number; cases: number; casesTotal?: number; excluded?: number; slowMs?: number;
    comparison: { status: string; tolerance: number; reasons: string[] };
    original: PerfSide | null; migrated: PerfSide | null } }
export interface PerfSide { requests: number; throughput: number | null; p50: number | null; p95: number | null; p99: number | null;
  errors: number; errorRate: number | null; memoryMb: { max: number; avg: number } | null }
export interface ManualResult { results: (HttpAnswer & { label: string; url: string })[]; state?: string; issues?: string[]; kind?: string | null }

// Java / Spring versions each migration target accepts (GET /target-versions)
export type TargetVersions = S['TargetVersions'];

/** Oracle APEX: analysis of the application exports and plan of jumps (mf.api.apex). */
export interface ApexFinding {
  rule: string; title: string; action: string; severity: 'BLOCK' | 'REVIEW' | 'INFO'; status: string; since: string | null; source: string | null;
  count: number; locations: number; where: string[];
}
export interface ApexApp {
  file: string; appId: string; name: string; owner: string; release: string; version: string; compatibilityMode: string | null; theme: string | null;
  stats: Record<string, number>; plugins: { name: string; displayName: string; type: string; version: string | null; uses: number }[];
  findings: ApexFinding[]; authentication: string[]; hosts: string[]; status: 'BLOCK' | 'REVIEW' | 'OK'; counts: Record<string, number>;
  schema: { owner: string; referenced: number; defined: number; missing: number; missingSample: string[]; schemas: string[] };
}
export interface ApexChange {
  rule: string; title: string; action: string; severity: 'BLOCK' | 'REVIEW' | 'INFO'; status: string; since: string | null; source: string | null; count: number; apps: string[];
}
export interface ApexPlan {
  source: string | null; target: string; mode: string; baseline: ApexChange[];
  hops: { version: string; from: string; changes: ApexChange[]; counts: Record<string, number> }[];
}
export interface ApexRun {
  id: string; projectId: string; jobId: string | null; mode: string; state: string; target: string; planMode: string; apps: ApexApp[]; plan: ApexPlan;
  summary: { apps: number; sourceVersions: string[]; totals: Record<string, number>; plugins: number; blocked: number; review: number; withSchema: number;
             artifacts?: { report?: string; data?: string } };
  createdBy: string; createdAt: string; job: any;
}
export interface ApexVersions { versions: string[]; yearly: string[]; note: string }

/** Odoo 12 -> 19 database migration run (mf.api.odoo): analyze (modules classified) or migrate (hops, reconciliation). */
export interface OdooModule {
  name: string; author: string; version?: string; origin: 'CORE' | 'OCA' | 'THIRD_PARTY'; outcome: 'OK' | 'WARN' | 'BLOCK'; reason: string;
  repository?: string | null; availability?: Record<string, boolean>; final?: string | null;
  path?: { hop: string; module: string; status: string; note?: string | null }[];
}
export interface OdooHop { version: string; state: string; exitCode: number | null; errors: number | null; baseVersion: string | null; logTail: string }
export interface OdooRun {
  id: string; projectId: string; jobId: string | null; mode: 'analyze' | 'migrate' | 'code' | 'flows' | 'preview'; state: string; analysisId: string | null;
  summary: Record<string, any>; modules: OdooModule[]; decisions: Record<string, { action: string; reason: string; by: string; at: string }>;
  hops: OdooHop[]; reconciliation: { check: string; label: string; before: string[] | null; after: string[] | null; state: string }[];
  createdBy: string; createdAt: string; job: any;
}

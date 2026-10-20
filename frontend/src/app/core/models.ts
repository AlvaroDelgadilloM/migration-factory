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
export type SourceType = 'local' | 'zip' | 'git';

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
  artifacts?: { evidence: string; responses: string };
  // automatic test from a migration (mf.tomcat_war.autotest)
  mode?: 'HUMO' | 'DATOS'; container?: string; modules?: string[]; generatedCases?: number; externalCalls?: string[];
  startup?: Record<'original' | 'migrado', { started: number | null; total: number | null; failed: string[] }>;
  configuration?: { files: number; replaced: number; generated: number; fromDataPackage: string[] } }
export interface ManualResult { results: (HttpAnswer & { label: string; url: string })[]; state?: string; issues?: string[]; kind?: string | null }

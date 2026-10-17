import { HttpClient, HttpParams } from '@angular/common/http';
import { inject, Injectable } from '@angular/core';
import { firstValueFrom } from 'rxjs';
import {
  Artifact, AuditEvent, Catalog, DiffFile, DiffFileDetail, Execution, Finding, FindingDetail, LogEvent, Member, Module,
  Browse, Capabilities, ChangeEntry, EffortEstimate, ExecutionResult, HttpCase, HttpTestRun, ManualResult, Modernization, WorkspaceMigration, DataStageMigration, ModernizationRule, Page, Permissions, Plan, Profile, Project, Route, Scan, Upload, Validation,
} from './models';

const B = '/api/v1';
type Params = Record<string, string | number | boolean | null | undefined>;

function params(p: Params = {}) {
  let hp = new HttpParams();
  for (const [k, v] of Object.entries(p)) if (v !== null && v !== undefined && v !== '') hp = hp.set(k, String(v));
  return hp;
}

/** Typed access to the Migration Factory API. Components never use HttpClient directly. */
/** UUID v4 for Idempotency-Key. crypto.randomUUID() exists only in secure contexts (HTTPS or localhost); over plain HTTP
 *  (e.g. http://<ip>:3240) the browser leaves it undefined, while crypto.getRandomValues() is available everywhere. */
export function uuid(): string {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40;  // version 4
  b[8] = (b[8] & 0x3f) | 0x80;  // RFC 4122 variant
  const h = Array.from(b, x => x.toString(16).padStart(2, '0')).join('');
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

@Injectable({ providedIn: 'root' })
export class Api {
  private http = inject(HttpClient);
  private get<T>(url: string, p?: Params) { return firstValueFrom(this.http.get<T>(B + url, { params: params(p) })); }
  private send<T>(method: string, url: string, body: unknown = null, headers: Record<string, string> = {}) {
    return firstValueFrom(this.http.request<T>(method, B + url, { body, headers }));
  }
  private key() { return uuid(); }

  // sources
  capabilities() { return this.get<Capabilities>('/capabilities'); }
  browse(path?: string | null) { return this.get<Browse>('/sources/browse', { path }); }
  /** Uploads the ZIP as the raw request body; the API validates size and structure before storing it. */
  uploadZip(file: Blob, name: string, migrationType: 'JAVA_CAMEL' | 'DATASTAGE_AIRFLOW' = 'JAVA_CAMEL') {
    return firstValueFrom(this.http.post<Upload>(`${B}/uploads`, file, {
      headers: { 'Content-Type': 'application/zip', 'X-Filename': encodeURIComponent(name), 'X-Migration-Type': migrationType } }));
  }

  replaceUpload(projectId: string, uploadId: string) {
    return this.send<Project>('PUT', `/projects/${projectId}/upload`, { uploadId });
  }

  // projects
  projects(p: Params) { return this.get<Page<Project>>('/projects', p); }
  project(id: string) { return this.get<Project>(`/projects/${id}`); }
  createProject(body: unknown) { return this.send<Project>('POST', '/projects', body); }
  updateProject(id: string, version: number, body: unknown) { return this.send<Project>('PATCH', `/projects/${id}`, body, { 'If-Match': String(version) }); }
  permissions(id: string) { return this.get<Permissions>(`/projects/${id}/permissions`); }
  members(id: string) { return this.get<Member[]>(`/projects/${id}/members`); }
  setMember(id: string, subjectId: string, role: string) { return this.send<Member>('PUT', `/projects/${id}/members`, { subjectId, role }); }
  deleteProject(id: string, confirm: string) { return this.send<void>('DELETE', `/projects/${id}?confirm=${encodeURIComponent(confirm)}`); }
  deleteExecution(id: string) { return this.send<void>('DELETE', `/executions/${id}`); }
  removeMember(id: string, subjectId: string) { return this.send<void>('DELETE', `/projects/${id}/members/${encodeURIComponent(subjectId)}`); }

  // scans & inventory
  scans(projectId: string, p: Params = {}) { return this.get<Page<Scan>>(`/projects/${projectId}/scans`, p); }
  startScan(projectId: string, body: unknown) { return this.send<{ scanId: string; jobId: string }>('POST', `/projects/${projectId}/scans`, body, { 'Idempotency-Key': this.key() }); }
  scan(id: string) { return this.get<Scan>(`/scans/${id}`); }
  modules(scanId: string) { return this.get<Module[]>(`/scans/${scanId}/modules`); }
  routes(scanId: string, p: Params) { return this.get<Page<Route>>(`/scans/${scanId}/routes`, p); }

  // findings
  findings(scanId: string, p: Params) { return this.get<Page<Finding>>(`/scans/${scanId}/findings`, p); }
  finding(id: string) { return this.get<FindingDetail>(`/findings/${id}`); }
  reviewFinding(id: string, version: number, decision: string, reason: string, minutes?: number | null) {
    return this.send<Finding>('PATCH', `/findings/${id}/review`, { decision, reason, minutes: minutes || null }, { 'If-Match': String(version) });
  }

  effortEstimate(scanId: string) { return this.get<EffortEstimate>(`/scans/${scanId}/effort-estimate`); }
  logEffort(projectId: string, body: unknown) { return this.send<unknown>('POST', `/projects/${projectId}/effort`, body); }

  // plans & executions
  profiles(p: Params = {}) { return this.get<Profile[]>('/profiles', p); }
  plans(scanId: string) { return this.get<Plan[]>(`/scans/${scanId}/plans`); }
  plan(id: string) { return this.get<Plan>(`/plans/${id}`); }
  createPlan(scanId: string, profileId: string) { return this.send<Plan>('POST', `/scans/${scanId}/plans`, { profileId }); }
  approvePlan(id: string, rowVersion: number) { return this.send<Plan>('POST', `/plans/${id}/approve`, null, { 'If-Match': String(rowVersion) }); }
  startExecution(planId: string, mode: 'preview' | 'apply') {
    return this.send<{ executionId: string; jobId: string }>('POST', `/plans/${planId}/executions`, { mode }, { 'Idempotency-Key': this.key() });
  }
  executions(projectId: string, p: Params = {}) { return this.get<Page<Execution>>(`/projects/${projectId}/executions`, p); }
  execution(id: string) { return this.get<Execution>(`/executions/${id}`); }
  cancelExecution(id: string) { return this.send<Execution>('POST', `/executions/${id}/cancel`); }
  rollback(id: string) { return this.send<Execution>('POST', `/executions/${id}/rollback`); }
  cancelJob(id: string) { return this.send<unknown>('POST', `/jobs/${id}/cancel`); }
  logs(jobId: string, after = 0) { return this.get<LogEvent[]>(`/jobs/${jobId}/logs`, { after, limit: 200 }); }
  diff(executionId: string, p: Params) { return this.get<Page<DiffFile>>(`/executions/${executionId}/diff`, p); }
  diffFile(id: string) { return this.get<DiffFileDetail>(`/diff-files/${id}`); }
  reviewDiff(id: string, version: number, decision: string, reason: string, minutes?: number | null) {
    return this.send<DiffFile>('POST', `/diff-files/${id}/review`, { decision, reason, minutes: minutes || null }, { 'If-Match': String(version) });
  }
  validations(executionId: string) { return this.get<Validation[]>(`/executions/${executionId}/validations`); }
  uploadEvidence(executionId: string, file: File) {
    return firstValueFrom(this.http.post<{ id: string }>(`${B}/executions/${executionId}/evidence-files`, file,
      { headers: { 'Content-Type': 'application/octet-stream', 'X-Filename': encodeURIComponent(file.name) } }));
  }
  recordEvidence(executionId: string, body: unknown) { return this.send<Validation>('POST', `/executions/${executionId}/validations`, body); }
  changes(executionId: string) { return this.get<ChangeEntry[]>(`/executions/${executionId}/changes`); }
  modernizationRules() { return this.get<ModernizationRule[]>('/modernization-rules'); }
  modernizations(executionId: string) { return this.get<Modernization[]>(`/executions/${executionId}/modernizations`); }
  modernization(id: string) { return this.get<Modernization>(`/modernizations/${id}`); }
  startModernization(executionId: string, approve: string[]) {
    return this.send<{ modernizationId: string; jobId: string }>('POST', `/executions/${executionId}/modernizations`, { approve }, { 'Idempotency-Key': this.key() });
  }
  pullRequest(executionId: string) { return this.send<unknown>('POST', `/executions/${executionId}/pull-request`, null, { 'Idempotency-Key': this.key() }); }

  // artifacts
  artifacts(projectId: string, p: Params) { return this.get<Page<Artifact>>(`/projects/${projectId}/artifacts`, p); }
  dataStageMigrations(projectId: string) { return this.get<DataStageMigration[]>(`/projects/${projectId}/datastage-migrations`); }
  startDataStageMigration(projectId: string, body: unknown) {
    return this.send<{ dataStageMigrationId: string; jobId: string }>('POST', `/projects/${projectId}/datastage-migrations`, body, { 'Idempotency-Key': this.key() });
  }
  workspaceMigrations(scanId: string) { return this.get<WorkspaceMigration[]>(`/scans/${scanId}/workspace-migrations`); }
  startWorkspaceMigration(scanId: string, body: unknown) {
    return this.send<{ workspaceMigrationId: string; jobId: string }>('POST', `/scans/${scanId}/workspace-migrations`, body, { 'Idempotency-Key': this.key() });
  }
  retryWorkspaceMigration(id: string, projects: string[]) {
    return this.send<{ workspaceMigrationId: string; jobId: string }>('POST', `/workspace-migrations/${id}/retry`, { projects }, { 'Idempotency-Key': this.key() });
  }
  executionResult(id: string) { return this.get<ExecutionResult>(`/executions/${id}/result`); }
  async downloadResult(id: string, name: string) {
    const blob = await firstValueFrom(this.http.get(`${B}/executions/${id}/download`, { responseType: 'blob' }));
    saveBlob(blob, name);
  }
  async download(id: string, name: string) {
    const blob = await firstValueFrom(this.http.get(`${B}/artifacts/${id}/download`, { responseType: 'blob' }));
    saveBlob(blob, name);
  }

  // pruebas (manual and automatic HTTP tests)
  manualTest(projectId: string, body: { request: HttpCase; targets: { label: string; url: string }[] }) {
    return this.send<ManualResult>('POST', `/projects/${projectId}/http-tests/manual`, body);
  }
  startHttpRun(projectId: string, body: unknown) {
    return this.send<{ httpTestRunId: string; jobId: string }>('POST', `/projects/${projectId}/http-tests/runs`, body, { 'Idempotency-Key': this.key() });
  }
  httpRuns(projectId: string) { return this.get<HttpTestRun[]>(`/projects/${projectId}/http-tests/runs`); }
  httpRun(id: string) { return this.get<HttpTestRun>(`/http-tests/runs/${id}`); }

  // catalog & audit
  catalogs() { return this.get<Catalog[]>('/rules'); }
  createCatalog(body: unknown) { return this.send<Catalog>('POST', '/rules', body); }
  activateCatalog(id: string) { return this.send<Catalog>('POST', `/rules/${id}/activate`); }
  setProfileStatus(id: string, status: string) { return firstValueFrom(this.http.post<Profile>(`${B}/profiles/${id}/status`, null, { params: params({ status }) })); }
  audit(p: Params) { return this.get<Page<AuditEvent>>('/audit', p); }
  async exportAudit(projectId?: string) {
    const blob = await firstValueFrom(this.http.get(`${B}/audit/export`, { params: params({ projectId }), responseType: 'blob' }));
    saveBlob(blob, 'audit.csv');
  }
}

export function saveBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

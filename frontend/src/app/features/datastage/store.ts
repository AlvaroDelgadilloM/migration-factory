import { computed, effect, inject, Injectable, signal } from '@angular/core';
import { Api } from '../../core/api';
import { UiError } from '../../core/errors';
import { DataStageMigration } from '../../core/models';
import { ProjectContext } from '../../core/project-context';
import { Loader } from '../../shared/state';
import { SeqFlow } from './flow-diagram';

export interface DsJob {
  name: string; jobType: string; complexity: string; score: number; migratability: string; confidence: string; state: string;
  dependsOn: string[]; calls: string[]; manualActions: string[]; dag: string | null; reasons: string[]; stages: number; sourceFile: string;
  steps: { task: string; kind: string; stage: string; status: string; issues: string[]; sqlFile: string | null }[];
  flow: SeqFlow | null; ownDag: boolean;
}
export interface DsAction { key: string; job: string; stage: string; category: string; severity: 'manual' | 'review'; source: string; text: string }

/** Latest DataStage → Airflow run of the selected project, shared by the Resumen / Jobs / Oleadas / Acciones pages. */
@Injectable({ providedIn: 'root' })
export class DataStageStore {
  private api = inject(Api);
  readonly ctx = inject(ProjectContext);
  readonly list = new Loader<DataStageMigration[]>();
  readonly busy = signal(false);
  readonly error = signal<UiError | null>(null);
  readonly current = computed(() => this.list.data()?.[0] ?? null);
  readonly running = computed(() => ['queued', 'running'].includes(this.current()?.state ?? ''));
  // free-form report written by the engine
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  readonly summary = computed<any>(() => this.current()?.summary ?? null);
  readonly jobs = computed<DsJob[]>(() => Object.entries((this.current()?.results ?? {}) as Record<string, any>).map(([name, r]) => ({
    name, jobType: r.jobType, complexity: r.complexity, score: r.score, migratability: r.migratability, confidence: r.confidence,
    state: r.state, dependsOn: r.dependsOn ?? [], calls: r.calls ?? [], manualActions: r.manualActions ?? [], dag: r.dag ?? null,
    reasons: r.reasons ?? [], stages: r.stages ?? 0, sourceFile: r.sourceFile ?? '', steps: r.steps ?? [], flow: r.flow ?? null, ownDag: !!r.ownDag })));
  readonly actions = computed<DsAction[]>(() => this.summary()?.actions ?? []);
  readonly actionState = computed(() => (this.current()?.actionState ?? {}) as Record<string, { done: boolean; by: string; at: string; note?: string }>);
  readonly pilot = computed(() => Object.fromEntries(Object.entries((this.summary()?.pilot ?? {}) as Record<string, string>).map(([k, v]) => [v, k])));
  readonly artifacts = computed(() => Object.entries((this.summary()?.artifacts ?? {}) as Record<string, string>)
    .map(([k, v]) => ({ name: k.replace(/^ds-[0-9a-f]+\./, ''), id: v })));
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  readonly reconciliation = computed<any>(() => (this.current()?.reconciliation as any)?.latest ?? null);
  /** cron per DAG: saved (applied by the next migration) and applied by this run */
  readonly schedules = computed(() => ((this.current()?.options as Record<string, unknown> | undefined)?.['schedules'] ?? {}) as Record<string, string>);
  readonly appliedSchedules = computed(() => (this.summary()?.schedules ?? {}) as Record<string, string>);
  /** doc 58: AI proposals of this run, by job (validated, never applied) */
  readonly aiProposals = computed(() => (this.summary()?.aiProposals ?? []) as { job: string; stage: string; link: string; column: string;
    expression: string; status: string; sql?: string; notes?: string; problems: string[] }[]);
  readonly airflowZip = computed(() => this.artifacts().find(a => a.name.endsWith('.zip')) ?? null);
  readonly startReason = computed(() => {
    if (!this.ctx.can('execute')) return `Su rol (${this.ctx.role()}) no permite migrar`;
    if (this.running()) return 'Ya hay una migración en curso';
    return null;
  });
  private loadedFor: string | null = null;

  constructor() {
    effect(() => {
      const id = this.ctx.projectId();
      if (id && id !== this.loadedFor && this.ctx.project()?.migrationType === 'DATASTAGE_AIRFLOW') {
        this.loadedFor = id;
        this.list.data.set(null);
        this.list.load(() => this.api.dataStageMigrations(id));
      }
    });
  }

  reload() { return this.list.reload(); }

  async start(timezone: string, aiAssist = false) {
    const id = this.ctx.projectId();
    if (!id) return;
    this.error.set(null);
    this.busy.set(true);
    try { await this.api.startDataStageMigration(id, { timezone: timezone.trim(), aiAssist }); await this.reload(); }
    catch (e) { this.error.set(UiError.from(e)); }
    finally { this.busy.set(false); }
  }

  /** Uploads reconciliation-result.json (run in the client's environment); the backend evaluates it and keeps the evidence. */
  async uploadReconciliation(result: unknown): Promise<boolean> {
    const m = this.current();
    if (!m) return false;
    this.error.set(null);
    this.busy.set(true);
    try {
      const updated = await this.api.uploadDataStageReconciliation(m.id, result);
      this.list.data.update(l => l ? [updated, ...l.slice(1)] : l);
      return true;
    } catch (e) { this.error.set(UiError.from(e)); return false; }
    finally { this.busy.set(false); }
  }

  /** Saves (or clears, with '') the cron of one DAG; applied by the next migration. */
  async setSchedule(job: string, cron: string): Promise<boolean> {
    const m = this.current();
    if (!m) return false;
    const next = { ...this.schedules() };
    if (cron.trim()) next[job] = cron.trim().replace(/\s+/g, ' '); else delete next[job];
    this.error.set(null);
    try {
      const updated = await this.api.setDataStageSchedules(m.id, next);
      this.list.data.update(l => l ? [updated, ...l.slice(1)] : l);
      return true;
    } catch (e) { this.error.set(UiError.from(e)); return false; }
  }

  async setAction(key: string, done: boolean) {
    const m = this.current();
    if (!m) return;
    try {
      const updated = await this.api.setDataStageAction(m.id, key, done);
      this.list.data.update(l => l ? [updated, ...l.slice(1)] : l);
    } catch (e) { this.error.set(UiError.from(e)); }
  }
}

export const STATE_ORDER = ['MANUAL_REVIEW', 'SEMIAUTOMATIC', 'AUTO_MIGRATED', 'BLOCKED', 'FAILED'];

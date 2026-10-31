import { computed, effect, inject, Injectable, signal } from '@angular/core';
import { Api } from '../../core/api';
import { UiError } from '../../core/errors';
import { ApexPlan, ApexRun, ApexVersions } from '../../core/models';
import { ProjectContext } from '../../core/project-context';
import { Loader } from '../../shared/state';

/** Oracle APEX runs of the selected project, shared by Resumen and Aplicaciones. */
@Injectable({ providedIn: 'root' })
export class ApexStore {
  private api = inject(Api);
  readonly ctx = inject(ProjectContext);
  readonly list = new Loader<ApexRun[]>();
  readonly versions = new Loader<ApexVersions>();
  readonly busy = signal(false);
  readonly error = signal<UiError | null>(null);
  readonly target = signal('26.1');
  readonly mode = signal<'yearly' | 'all'>('yearly');
  /** the plan for another target or route, without reading the exports again */
  private replanned = signal<ApexPlan | null>(null);
  readonly running = computed(() => (this.list.data() ?? []).find(r => ['queued', 'running'].includes(r.state)) ?? null);
  readonly latest = computed(() => (this.list.data() ?? []).find(r => ['succeeded', 'partial_success'].includes(r.state)) ?? null);
  readonly plan = computed(() => this.replanned() ?? this.latest()?.plan ?? null);
  /** what the project's ZIP brought, known before any analysis */
  readonly uploaded = computed(() => ((this.ctx.project()?.upload as Record<string, any> | null | undefined)?.['structure']?.['apexApps'] ?? []) as
    { file: string; appId: string; name: string; owner: string; release: string }[]);
  private loadedFor: string | null = null;

  constructor() {
    effect(() => {
      const id = this.ctx.projectId();
      if (id && id !== this.loadedFor && this.ctx.project()?.migrationType === 'APEX') {
        this.loadedFor = id;
        this.replanned.set(null);
        this.list.data.set(null);
        this.list.load(() => this.api.apexRuns(id));
        this.versions.load(() => this.api.apexVersions());
      }
    });
  }

  reload() { this.replanned.set(null); return this.list.reload(); }

  async analyze() {
    const id = this.ctx.projectId();
    if (!id) return;
    this.error.set(null);
    this.busy.set(true);
    try { await this.api.startApexAnalysis(id, this.target(), this.mode()); await this.reload(); }
    catch (e) { this.error.set(UiError.from(e)); }
    finally { this.busy.set(false); }
  }

  async replan(target: string, mode: 'yearly' | 'all') {
    this.target.set(target);
    this.mode.set(mode);
    const run = this.latest();
    if (!run) return;
    this.error.set(null);
    try { this.replanned.set(await this.api.apexPlan(run.id, target, mode)); }
    catch (e) { this.error.set(UiError.from(e)); }
  }
}

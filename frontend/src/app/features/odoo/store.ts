import { computed, effect, inject, Injectable, signal } from '@angular/core';
import { Api } from '../../core/api';
import { UiError } from '../../core/errors';
import { OdooRun } from '../../core/models';
import { ProjectContext } from '../../core/project-context';
import { Loader } from '../../shared/state';

/** Odoo 12 -> 19 runs of the selected project, shared by Resumen and Módulos. */
@Injectable({ providedIn: 'root' })
export class OdooStore {
  private api = inject(Api);
  readonly ctx = inject(ProjectContext);
  readonly list = new Loader<OdooRun[]>();
  readonly busy = signal(false);
  readonly error = signal<UiError | null>(null);
  readonly running = computed(() => (this.list.data() ?? []).find(r => ['queued', 'running'].includes(r.state)) ?? null);
  readonly analysis = computed(() => (this.list.data() ?? []).find(r => r.mode === 'analyze' && r.state === 'succeeded') ?? null);
  readonly migration = computed(() => (this.list.data() ?? []).find(r => r.mode === 'migrate') ?? null);
  /** blocking modules of the latest analysis still without the architect's decision */
  readonly pending = computed(() => {
    const a = this.analysis();
    return (a?.modules ?? []).filter(m => m.outcome === 'BLOCK' && a?.decisions?.[m.name]?.action !== 'uninstall');
  });
  /** suggested data fixes of the latest analysis (mf.odoo.fixes) still without a decision: apply on the copy, or go on without */
  readonly pendingFixes = computed(() => {
    const a = this.analysis();
    return ((a?.summary?.['fixes'] ?? []) as { id: string }[]).filter(f => !['apply', 'ignore'].includes(a?.decisions?.['fix:' + f.id]?.action ?? ''));
  });
  private loadedFor: string | null = null;

  constructor() {
    effect(() => {
      const id = this.ctx.projectId();
      if (id && id !== this.loadedFor && this.ctx.project()?.migrationType === 'ODOO') {
        this.loadedFor = id;
        this.list.data.set(null);
        this.list.load(() => this.api.odooRuns(id));
      }
    });
  }

  reload() { return this.list.reload(); }

  async start(mode: 'analyze' | 'migrate') {
    const id = this.ctx.projectId();
    if (!id) return;
    this.error.set(null);
    this.busy.set(true);
    try { await this.api.startOdoo(id, mode); await this.reload(); }
    catch (e) { this.error.set(UiError.from(e)); }
    finally { this.busy.set(false); }
  }

  async decide(module: string, action: 'uninstall' | 'apply' | 'ignore' | 'pending', reason: string): Promise<boolean> {
    const a = this.analysis();
    if (!a) return false;
    this.error.set(null);
    try {
      const updated = await this.api.decideOdoo(a.id, module, action, reason);
      this.list.data.update(l => l ? l.map(r => r.id === updated.id ? updated : r) : l);
      return true;
    } catch (e) { this.error.set(UiError.from(e)); return false; }
  }
}

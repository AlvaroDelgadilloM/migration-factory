import { Component, computed, effect, inject, signal } from '@angular/core';
import { NavigationEnd, Router, RouterLink } from '@angular/router';
import { filter } from 'rxjs';
import { Api } from '../core/api';
import { Execution, HttpTestRun, Plan, Scan, WorkspaceMigration } from '../core/models';
import { ProjectContext } from '../core/project-context';
import { humanStatus } from './ui';
import { DataStageStore } from '../features/datastage/store';
import { OdooStore } from '../features/odoo/store';
import { ApexStore } from '../features/apex/store';

type State = 'done' | 'current' | 'running' | 'error' | 'todo';
interface FlowStep { key: string; label: string; route: string; state: State; detail: string }

/** Dónde está cada proyecto en su flujo, con una siguiente acción recomendada. Java/Camel: Analizar → Hallazgos →
 *  Plan → Ejecución → Validación (o Migración por oleadas → Pruebas automáticas en workspace). DataStage: Resumen →
 *  Jobs → Oleadas → Acciones → Reconciliación. Odoo: Resumen → Módulos → Código → Pruebas. APEX: Resumen →
 *  Aplicaciones. Los estados se muestran en lenguaje humano; el código técnico queda en el tooltip del tag.
 *  Read-only: solo enlaza a cada pantalla. */
@Component({
  selector: 'mf-flow-progress',
  imports: [RouterLink],
  template: `
    @if (steps().length) {
      <nav class="flow" aria-label="Avance de la migración">
        <ol>
          @for (s of steps(); track s.key; let i = $index) {
            <li [class]="'st-' + s.state" [attr.aria-current]="s.state === 'current' ? 'step' : null">
              <a [routerLink]="['/p', ctx.projectId(), s.route]">
                <span class="dot" aria-hidden="true">
                  @switch (s.state) {
                    @case ('done') { <i class="pi pi-check"></i> }
                    @case ('running') { <i class="pi pi-spin pi-spinner"></i> }
                    @case ('error') { <i class="pi pi-times"></i> }
                    @default { {{ i + 1 }} }
                  }
                </span>
                <span class="txt"><strong>{{ s.label }}</strong><small>{{ s.detail }}</small></span>
              </a>
            </li>
          }
        </ol>
        @if (next(); as n) {
          <a class="next" [routerLink]="['/p', ctx.projectId(), n.route]">{{ n.text }} <i class="pi pi-arrow-right" aria-hidden="true"></i></a>
        }
      </nav>
    }`,
  styles: [`
    .flow{display:flex;align-items:center;gap:16px;background:#fff;border:1px solid var(--mf-line);border-radius:14px;padding:10px 14px;
      margin:0 0 20px;box-shadow:var(--mf-shadow);flex-wrap:wrap}
    ol{list-style:none;margin:0;padding:0;display:flex;flex:1;gap:4px;min-width:0;flex-wrap:wrap}
    li{flex:1 1 9rem;position:relative}
    li a{display:flex;gap:10px;align-items:center;padding:6px 8px;border-radius:10px;color:var(--mf-ink);text-decoration:none}
    li a:hover{background:#fdf8e7;text-decoration:none}
    .dot{width:28px;height:28px;flex:0 0 28px;border-radius:50%;display:grid;place-items:center;font-size:12px;font-weight:700;
      background:#eef3f7;color:#536679}
    .txt{display:flex;flex-direction:column;min-width:0}.txt strong{font-size:13.5px}
    .txt small{font-size:13px;color:var(--mf-muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    .st-done .dot{background:#12b76a;color:#fff}.st-current .dot{background:var(--mf-amber);color:var(--mf-coal);box-shadow:0 0 0 4px var(--mf-amber-soft)}
    .st-running .dot{background:#0a7ea4;color:#fff}.st-error .dot{background:#d92d20;color:#fff}.st-todo strong{color:var(--mf-muted)}
    .next{white-space:nowrap;font-weight:600;font-size:13.5px;background:var(--mf-amber);color:var(--mf-coal);padding:8px 14px;border-radius:10px}
    .next:hover{background:#d9a41c;text-decoration:none}
    @media (max-width:860px){li{flex-basis:45%}.next{width:100%;text-align:center}}
  `],
})
export class FlowProgress {
  ctx = inject(ProjectContext);
  private api = inject(Api);
  private ds = inject(DataStageStore);
  private odoo = inject(OdooStore);
  private apex = inject(ApexStore);
  private scan = signal<Scan | null>(null);
  private plan = signal<Plan | null>(null);
  private exec = signal<Execution | null>(null);
  private wsMigration = signal<WorkspaceMigration | null>(null);
  private wsRun = signal<HttpTestRun | null>(null);
  private loadedAt = signal(0);

  constructor() {
    const router = inject(Router);
    router.events.pipe(filter(e => e instanceof NavigationEnd)).subscribe(() => this.loadedAt.set(Date.now()));
    effect(() => {
      const id = this.ctx.projectId(), p = this.ctx.project();
      this.loadedAt();
      if (id && p && p.migrationType !== 'DATASTAGE_AIRFLOW') this.load(id);
    });
  }

  private async load(id: string) {
    try {
      const scans = await this.api.scans(id, { limit: 10 });
      const last = scans.items[0] ?? null, ok = scans.items.find(s => s.status === 'succeeded') ?? null;
      this.scan.set(last && ['queued', 'running'].includes(last.status) ? last : ok ?? last);
      if (ok && isMultiProject(ok)) {
        const wms = await this.api.workspaceMigrations(ok.id);
        const wm = wms.sort((a, b) => String(b.createdAt).localeCompare(String(a.createdAt)))[0] ?? null;
        this.wsMigration.set(wm);
        this.wsRun.set(wm ? (await this.api.httpRuns(id)).find(r => r.workspaceMigrationId === wm.id) ?? null : null);
        return;
      }
      this.wsMigration.set(null);
      const plans = ok ? await this.api.plans(ok.id) : [];
      this.plan.set(plans.sort((a, b) => b.version - a.version)[0] ?? null);
      const ex = await this.api.executions(id, { limit: 1 });
      this.exec.set(ex.items[0] ?? null);
    } catch { /* the page itself shows its own errors; the bar just stays as it was */ }
  }

  steps = computed<FlowStep[]>(() => {
    const p = this.ctx.project();
    if (!p) return [];
    if (p.migrationType === 'DATASTAGE_AIRFLOW') return this.dsSteps();
    if (p.migrationType === 'ODOO') return this.odooSteps();
    if (p.migrationType === 'APEX') return this.apexSteps();
    const s = this.scan(), pl = this.plan(), ex = this.exec();
    const findings = Number((s?.summary as Record<string, unknown> | undefined)?.['findings'] ?? 0);
    const exStatus = String((ex?.summary as Record<string, unknown> | undefined)?.['status'] ?? '');
    const scanState: State = !s ? 'current' : ['queued', 'running'].includes(s.status) ? 'running' : s.status === 'succeeded' ? 'done' : 'error';
    const first = [
      { key: 'scan', label: 'Analizar', route: 'inventory', state: scanState,
        detail: !s ? 'sin análisis' : scanState === 'running' ? 'en curso…' : scanState === 'done' ? new Date(s.createdAt).toLocaleDateString() : 'falló' },
      { key: 'findings', label: 'Hallazgos', route: 'findings', state: (scanState === 'done' ? 'done' : 'todo') as State,
        detail: scanState === 'done' ? `${findings} hallazgo(s)` : '—' },
    ];
    if (s && isMultiProject(s)) {
      const wm = this.wsMigration(), run = this.wsRun(), busy = (st?: string) => ['queued', 'running'].includes(st ?? '');
      const migState: State = scanState !== 'done' ? 'todo' : !wm ? 'current' : busy(wm.state) ? 'running'
        : ['failed', 'timed_out', 'cancelled'].includes(wm.state) ? 'error' : 'done';
      const testState: State = migState !== 'done' ? 'todo' : !run ? 'current' : busy(run.state) ? 'running' : run.state === 'failed' ? 'error' : 'done';
      return [...first,
        { key: 'wsmig', label: 'Migración por oleadas', route: 'inventory', state: migState,
          detail: !wm ? 'en Inventario' : `${wm.target} · ${humanStatus(wm.state)}` },
        { key: 'tests', label: 'Pruebas automáticas', route: 'pruebas/automaticas', state: testState,
          detail: !run ? 'sin probar' : humanStatus(String((run.summary as Record<string, unknown> | null)?.['status'] ?? run.state)) },
      ];
    }
    const planState: State = scanState !== 'done' ? 'todo' : !pl ? 'current' : pl.status === 'approved' ? 'done' : 'current';
    const exRunning = ex && ['queued', 'running'].includes(ex.state);
    const execState: State = planState !== 'done' ? 'todo' : !ex ? 'current' : exRunning ? 'running'
      : ['failed', 'timed_out', 'cancelled'].includes(ex.state) ? 'error' : 'done';
    const valState: State = execState !== 'done' ? 'todo' : exStatus === 'SUCCEEDED' ? 'done' : 'current';
    return [...first,
      { key: 'plan', label: 'Plan', route: 'plan', state: planState,
        detail: !pl ? 'sin plan' : `v${pl.version} · ${pl.status === 'approved' ? 'aprobado' : pl.status === 'draft' ? 'borrador' : humanStatus(pl.status)}` },
      { key: 'exec', label: 'Ejecución', route: 'execution', state: execState,
        detail: !ex ? 'sin ejecutar' : `${ex.mode === 'apply' ? 'aplicada' : 'previsualización'} · ${exRunning ? 'en curso' : humanStatus(ex.state)}` },
      { key: 'val', label: 'Validación', route: 'validation', state: valState,
        detail: !exStatus ? '—' : exStatus === 'SUCCEEDED' ? 'completa' : exStatus === 'PARTIAL_SUCCESS' ? 'con pendientes' : humanStatus(exStatus) },
    ];
  });


  /** DataStage → Airflow: Resumen → Jobs → Oleadas → Acciones → Reconciliación, desde el DataStageStore. */
  private dsSteps(): FlowStep[] {
    const m = this.ds.current(), jobs = this.ds.jobs(), actions = this.ds.actions();
    const waves = (this.ds.summary()?.waves ?? []) as unknown[];
    const rec = this.ds.reconciliation();
    const done = actions.filter(a => this.ds.actionState()[a.key]?.done).length;
    const mState: State = !m ? 'current' : ['queued', 'running'].includes(m.state ?? '') ? 'running'
      : m.state === 'succeeded' ? 'done' : ['failed', 'timed_out', 'cancelled'].includes(m.state ?? '') ? 'error' : 'current';
    const jobsState: State = !m ? 'todo' : jobs.length ? 'done' : 'current';
    const wavesState: State = jobsState !== 'done' ? 'todo' : waves.length ? 'done' : 'current';
    const actState: State = wavesState !== 'done' ? 'todo' : !actions.length ? 'current' : done === actions.length ? 'done' : 'current';
    const recState: State = actState !== 'done' ? 'todo' : !rec ? 'current'
      : ['VERIFIED', 'EQUAL'].includes(String(rec.status ?? '')) ? 'done' : 'current';
    return [
      { key: 'ds', label: 'Resumen', route: 'ds', state: mState,
        detail: !m ? 'sin migrar' : mState === 'running' ? 'en curso…' : humanStatus(m.state) },
      { key: 'dsjobs', label: 'Jobs', route: 'ds/jobs', state: jobsState,
        detail: jobs.length ? `${jobs.length} jobs` : '—' },
      { key: 'dswaves', label: 'Oleadas', route: 'ds/oleadas', state: wavesState,
        detail: waves.length ? `${waves.length} oleadas` : '—' },
      { key: 'dsactions', label: 'Acciones', route: 'ds/acciones', state: actState,
        detail: actions.length ? `${done}/${actions.length} resueltas` : '—' },
      { key: 'dsrec', label: 'Reconciliación', route: 'ds/reconciliacion', state: recState,
        detail: !rec ? 'sin subir' : humanStatus(String(rec.status ?? '')) },
    ];
  }

  /** Odoo 12 → 19: Resumen → Módulos → Código → Pruebas, desde el OdooStore. */
  private odooSteps(): FlowStep[] {
    const a = this.odoo.analysis(), c = this.odoo.code(), f = this.odoo.flows(), r = this.odoo.running();
    const pending = this.odoo.pending().length;
    const codeState: State = r?.mode === 'code' ? 'running' : !c ? 'current'
      : c.state === 'succeeded' ? 'done' : ['failed', 'timed_out', 'cancelled'].includes(c.state ?? '') ? 'error' : 'current';
    const flowState: State = r?.mode === 'flows' ? 'running' : !f ? 'current'
      : f.state === 'succeeded' ? 'done' : ['failed', 'timed_out', 'cancelled'].includes(f.state ?? '') ? 'error' : 'current';
    return [
      { key: 'odoo', label: 'Resumen', route: 'odoo', state: r?.mode === 'analyze' || r?.mode === 'migrate' ? 'running' : a ? 'done' : 'current',
        detail: a ? 'base analizada' : this.odoo.codeOnly() ? 'solo código' : 'sin analizar' },
      { key: 'modulos', label: 'Módulos', route: 'odoo/modulos', state: !a ? 'todo' : pending ? 'current' : 'done',
        detail: !a ? '—' : pending ? `${pending} bloquean` : 'sin bloqueos' },
      { key: 'codigo', label: 'Código', route: 'odoo/codigo', state: codeState,
        detail: !c ? 'sin portar' : humanStatus(c.state) },
      { key: 'pruebas', label: 'Pruebas', route: 'odoo/pruebas', state: flowState,
        detail: !f ? 'sin probar' : humanStatus(f.state) },
    ];
  }

  /** Oracle APEX: Resumen → Aplicaciones, desde el ApexStore. */
  private apexSteps(): FlowStep[] {
    const latest = this.apex.latest(), plan = this.apex.plan(), r = this.apex.running();
    const apps = this.apex.uploaded().length;
    return [
      { key: 'apex', label: 'Resumen', route: 'apex', state: r ? 'running' : latest ? 'done' : 'current',
        detail: latest ? 'análisis listo' : apps ? `${apps} app(s) por analizar` : 'sin analizar' },
      { key: 'apps', label: 'Aplicaciones', route: 'apex/aplicaciones', state: !latest ? 'todo' : plan ? 'done' : 'current',
        detail: plan ? 'plan listo' : 'sin plan' },
    ];
  }

  next = computed(() => {
    const cur = this.steps().find(s => s.state === 'current' || s.state === 'error');
    if (!cur) return null;
    const text: Record<string, string> = { scan: 'Analizar el proyecto', findings: 'Revisar hallazgos', plan: 'Crear o aprobar el plan',
      exec: 'Ejecutar la migración', val: 'Revisar la validación', wsmig: 'Migrar por oleadas', tests: 'Probar la migración',
      ds: 'Migrar el proyecto', dsjobs: 'Revisar los jobs', dswaves: 'Ver el orden por oleadas', dsactions: 'Resolver las acciones manuales',
      dsrec: 'Subir la reconciliación', odoo: 'Analizar la base', modulos: 'Revisar los módulos que bloquean',
      codigo: 'Portar el código a 19', pruebas: 'Probar los flujos de negocio', apex: 'Analizar las aplicaciones',
      apps: 'Revisar las aplicaciones' };
    return { route: cur.route, text: cur.state === 'error' ? `Revisar ${cur.label.toLowerCase()}` : text[cur.key] };
  });
}

/** A workspace with several projects and none selected: migrated as a whole (Migración por oleadas), never with a plan. */
export function isMultiProject(s: Scan): boolean {
  const ws = (s.summary as Record<string, unknown> | null)?.['workspace'] as { repositoryType?: string; selectedProject?: string } | undefined;
  return ws?.repositoryType === 'MULTI_PROJECT_REPOSITORY' && !ws?.selectedProject;
}

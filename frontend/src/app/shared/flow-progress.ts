import { Component, computed, effect, inject, signal } from '@angular/core';
import { NavigationEnd, Router, RouterLink } from '@angular/router';
import { filter } from 'rxjs';
import { Api } from '../core/api';
import { Execution, HttpTestRun, Plan, Scan, WorkspaceMigration } from '../core/models';
import { ProjectContext } from '../core/project-context';

type State = 'done' | 'current' | 'running' | 'error' | 'todo';
interface FlowStep { key: string; label: string; route: string; state: State; detail: string }

/** Where a Java/Camel project is in Analizar → Hallazgos → Plan → Ejecución → Validación, from its latest scan, plan and
 *  execution, with one recommended next action. A workspace of several projects (a Fuse/Karaf one) has no plan: it goes
 *  Analizar → Hallazgos → Migración por oleadas (Inventario) → Pruebas automáticas. Read-only: it only links to each screen. */
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
    li a:hover{background:#f4f8fa;text-decoration:none}
    .dot{width:28px;height:28px;flex:0 0 28px;border-radius:50%;display:grid;place-items:center;font-size:12px;font-weight:700;
      background:#eef3f7;color:#536679}
    .txt{display:flex;flex-direction:column;min-width:0}.txt strong{font-size:13.5px}
    .txt small{font-size:12px;color:var(--mf-muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    .st-done .dot{background:#12b76a;color:#fff}.st-current .dot{background:var(--mf-teal);color:#fff;box-shadow:0 0 0 4px var(--mf-teal-soft)}
    .st-running .dot{background:#0a7ea4;color:#fff}.st-error .dot{background:#d92d20;color:#fff}.st-todo strong{color:var(--mf-muted)}
    .next{white-space:nowrap;font-weight:600;font-size:13.5px;background:var(--mf-teal);color:#fff;padding:8px 14px;border-radius:10px}
    .next:hover{background:var(--mf-teal-2);text-decoration:none}
    @media (max-width:860px){li{flex-basis:45%}.next{width:100%;text-align:center}}
  `],
})
export class FlowProgress {
  ctx = inject(ProjectContext);
  private api = inject(Api);
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
    if (!p || p.migrationType === 'DATASTAGE_AIRFLOW') return [];
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
          detail: !wm ? 'en Inventario' : `${wm.target} · ${wm.state}` },
        { key: 'tests', label: 'Pruebas automáticas', route: 'pruebas/automaticas', state: testState,
          detail: !run ? 'sin probar' : String((run.summary as Record<string, unknown> | null)?.['status'] ?? run.state) },
      ];
    }
    const planState: State = scanState !== 'done' ? 'todo' : !pl ? 'current' : pl.status === 'approved' ? 'done' : 'current';
    const exRunning = ex && ['queued', 'running'].includes(ex.state);
    const execState: State = planState !== 'done' ? 'todo' : !ex ? 'current' : exRunning ? 'running'
      : ['failed', 'timed_out', 'cancelled'].includes(ex.state) ? 'error' : 'done';
    const valState: State = execState !== 'done' ? 'todo' : exStatus === 'SUCCEEDED' ? 'done' : 'current';
    return [...first,
      { key: 'plan', label: 'Plan', route: 'plan', state: planState,
        detail: !pl ? 'sin plan' : `v${pl.version} · ${pl.status === 'approved' ? 'aprobado' : pl.status === 'draft' ? 'borrador' : pl.status}` },
      { key: 'exec', label: 'Ejecución', route: 'execution', state: execState,
        detail: !ex ? 'sin ejecutar' : `${ex.mode === 'apply' ? 'aplicada' : 'previsualización'} · ${exRunning ? 'en curso' : ex.state}` },
      { key: 'val', label: 'Validación', route: 'validation', state: valState,
        detail: !exStatus ? '—' : exStatus === 'SUCCEEDED' ? 'completa' : exStatus === 'PARTIAL_SUCCESS' ? 'con pendientes' : exStatus.toLowerCase() },
    ];
  });

  next = computed(() => {
    const cur = this.steps().find(s => s.state === 'current' || s.state === 'error');
    if (!cur) return null;
    const text: Record<string, string> = { scan: 'Analizar el proyecto', findings: 'Revisar hallazgos', plan: 'Crear o aprobar el plan',
      exec: 'Ejecutar la migración', val: 'Revisar la validación', wsmig: 'Migrar por oleadas', tests: 'Probar la migración' };
    return { route: cur.route, text: cur.state === 'error' ? `Revisar ${cur.label.toLowerCase()}` : text[cur.key] };
  });
}

/** A workspace with several projects and none selected: migrated as a whole (Migración por oleadas), never with a plan. */
export function isMultiProject(s: Scan): boolean {
  const ws = (s.summary as Record<string, unknown> | null)?.['workspace'] as { repositoryType?: string; selectedProject?: string } | undefined;
  return ws?.repositoryType === 'MULTI_PROJECT_REPOSITORY' && !ws?.selectedProject;
}

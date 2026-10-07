import { Component, computed, input, output } from '@angular/core';
import { MessageModule } from 'primeng/message';
import { TableModule } from 'primeng/table';
import { ActionButton, StatusTag } from '../../shared/ui';

interface WsProject { name: string; dir: string; analysisStatus?: string; analysis?: string; contextMissing?: string[]; error?: string | null; technologies?: string[] }
interface Ws {
  repositoryType: string; workspaceRoot: string; analysisStatus?: string;
  workspaceBuild?: { status: string; reason: string | null; aggregatorPom: string };
  analysisWaves?: { wave: string; projects: string[]; status: string; note?: string; results?: Record<string, string> }[];
  counts?: { projectsDiscovered: number; projectsAnalyzed: number; projectsFailed: number }; selectedProject?: string | null; projects: WsProject[];
  graph?: { edges: { from: string; to: string }[] };
  order?: { waves: { wave: number; projects: string[] }[]; cycles: { projects: string[] }[]; blocked: Record<string, string> };
  shared?: { metrics: Record<string, string>; commonRules: { id: string; ruleId: string; projects: string[]; target: string }[];
             findingsByProject: Record<string, Record<string, number>> };
}

/** Doc 17: a repository with several Maven projects and no aggregator is shown as a workspace, never as an error. */
@Component({
  selector: 'mf-workspace',
  imports: [MessageModule, TableModule, ActionButton, StatusTag],
  template: `
    <p-message severity="info">
      <span><strong>{{ ws().repositoryType }}</strong> · workspace <code>{{ ws().workspaceRoot }}</code> · {{ ws().projects.length }} proyectos ·
        sin POM agregador (no se crea uno). @if (ws().counts; as c) { Analizados {{ c.projectsAnalyzed }}/{{ c.projectsDiscovered }}, con fallo {{ c.projectsFailed }}. }
        @if (ws().workspaceBuild?.status === 'SKIPPED') { Build Maven del workspace: <strong>no aplica</strong> (sin POM agregador); cada proyecto se construye con su propio pom.xml. }
        Para planificar y migrar, analice un proyecto concreto; respete el orden por oleadas.</span>
    </p-message>
    @if (ws().analysisWaves?.length) {
      <h3>Análisis por oleadas <small class="muted">{{ ws().analysisStatus }}</small></h3>
      <div class="waves">@for (w of ws().analysisWaves!; track w.wave) {
        <div class="wave"><strong>Wave {{ w.wave }}</strong> <small class="muted">{{ w.status }}</small>@if (w.note) { <br /><small class="muted">{{ w.note }}</small> }
          <ul>@for (n of w.projects; track n) { <li><span [class]="'ic ' + icon(w.results?.[n])" aria-hidden="true">{{ glyph(w.results?.[n]) }}</span> {{ n }}
            <small class="muted">{{ w.results?.[n] }}</small>@if (missing(n).length) { <br /><small class="muted">contexto incompleto: {{ missing(n).join(', ') }}</small> }</li> }</ul></div> }
      </div>
    }
    <p-table [value]="rows()">
      <ng-template #header><tr><th scope="col">Proyecto</th><th scope="col">Análisis</th><th scope="col">Oleada</th><th scope="col">Depende de</th><th scope="col">Hallazgos</th><th scope="col"></th></tr></ng-template>
      <ng-template #body let-r><tr>
        <td><strong>{{ r.name }}</strong><br /><small class="mono muted">{{ r.dir }}</small></td>
        <td><mf-status [value]="r.analysisStatus ?? 'PENDING'" />@if (r.error) { <br /><small class="blocked">{{ r.error }}</small> }</td>
        <td>@if (r.blocked) { <span class="blocked">{{ r.blocked }}</span> } @else { {{ r.wave ?? '—' }} }</td>
        <td><small>{{ r.deps.join(', ') || '—' }}</small></td><td>{{ r.findings }}</td>
        <td><mf-action label="Analizar proyecto" icon="pi pi-play" [outlined]="true" [disabledReason]="disabledReason()" (run)="analyze.emit(r.dir)" /></td>
      </tr></ng-template>
    </p-table>
    @if (ws().order?.cycles?.length) {
      <p-message severity="warn">DEPENDENCY_CYCLE: @for (c of ws().order!.cycles; track $index) { <code>{{ c.projects.join(' → ') }}</code> }
        — solo estos proyectos (y los que dependen de ellos) quedan fuera del orden automático.</p-message>
    }
    @if (ws().shared; as sh) {
      <h3>Hallazgos del workspace</h3>
      <div class="cards">@for (m of metrics(); track m[0]) { <article><b>{{ m[1] }}</b> {{ m[0] }}</article> }</div>
      @if (sh.commonRules.length) {
        <p-table [value]="sh.commonRules">
          <ng-template #header><tr><th scope="col">Regla común</th><th scope="col">Detectado</th><th scope="col">Proyectos</th><th scope="col">Objetivo</th></tr></ng-template>
          <ng-template #body let-c><tr><td>{{ c.id }}</td><td>{{ c.ruleId }}</td><td><small>{{ c.projects.join(', ') }}</small></td><td><small>{{ c.target }}</small></td></tr></ng-template>
        </p-table>
      }
    }`,
  styles: [`.blocked{color:#b71c1c;font-weight:600}.waves{display:flex;gap:1rem;flex-wrap:wrap;margin:.5rem 0}
    .wave{border:1px solid var(--p-content-border-color,#ddd);border-radius:6px;padding:.5rem .75rem;min-width:14rem}.wave ul{margin:.25rem 0;padding-left:1rem;list-style:none}
    .ok{color:#2e7d32}.warn{color:#e65100}.bad{color:#b71c1c}`],
})
export class WorkspacePanel {
  ws = input.required<Ws>();
  disabledReason = input<string | null>(null);
  analyze = output<string>();
  metrics = computed(() => Object.entries(this.ws().shared?.metrics ?? {}));
  glyph(s?: string) { return s === 'ANALYSIS_SUCCEEDED' ? '✓' : s === 'ANALYSIS_PARTIAL' ? '⚠' : s === 'ANALYSIS_FAILED' ? '✗' : '·'; }
  icon(s?: string) { return s === 'ANALYSIS_SUCCEEDED' ? 'ok' : s === 'ANALYSIS_PARTIAL' ? 'warn' : s === 'ANALYSIS_FAILED' ? 'bad' : ''; }
  missing(name: string) { return this.ws().projects.find(p => p.name === name)?.contextMissing ?? []; }
  rows = computed(() => {
    const w = this.ws();
    const wave: Record<string, number> = {};
    (w.order?.waves ?? []).forEach(x => x.projects.forEach(p => (wave[p] = x.wave)));
    return w.projects.map(p => ({
      ...p, wave: wave[p.name], blocked: w.order?.blocked?.[p.name] ?? null,
      deps: (w.graph?.edges ?? []).filter(e => e.from === p.name).map(e => e.to),
      findings: Object.values(w.shared?.findingsByProject?.[p.name] ?? {}).reduce((a, b) => a + b, 0),
    }));
  });
}

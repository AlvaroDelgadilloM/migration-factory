import { Component, computed, effect, inject, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { CheckboxModule } from 'primeng/checkbox';
import { MessageModule } from 'primeng/message';
import { TableModule } from 'primeng/table';
import { Api } from '../../core/api';
import { UiError } from '../../core/errors';
import { Execution, Modernization, ModernizationRule } from '../../core/models';
import { ProjectContext } from '../../core/project-context';
import { Loader } from '../../shared/state';
import { ActionButton, JobLog, StateView, StatusTag, TrafficLight } from '../../shared/ui';

const LEVEL: Record<string, string> = { SAFE: 'AUTO', REFACTOR: 'REVIEW', ARCHITECTURE: 'MANUAL' };

/** 12-PROMPT-CLAUDE-MODERNIZATION: modernize a copy of the candidate; SAFE/REFACTOR gated by build+tests, ARCHITECTURE proposed only. */
@Component({
  selector: 'mf-modernization',
  imports: [FormsModule, CheckboxModule, MessageModule, TableModule, ActionButton, JobLog, StateView, StatusTag, TrafficLight],
  template: `
    <p class="muted">Se trabaja sobre una copia del candidato (candidate.zip no cambia). Cada refactor: snapshot → aplicar → <code>mvn clean test</code>
      → rollback automático si falla o corren menos pruebas. ARCHITECTURE nunca se aplica.</p>
    @if (error(); as e) { <p-message severity="error">{{ e.code }}: {{ e.message }}</p-message> }
    <fieldset class="approvals">
      <legend>Refactors que cambian contratos públicos (requieren aprobación de arquitecto/owner)</legend>
      @for (r of contractRules(); track r.id) {
        <span class="check"><p-checkbox [inputId]="r.id" [value]="r.id" [(ngModel)]="approve" [disabled]="!ctx.can('plan')" />
          <label [for]="r.id">{{ r.id }} — {{ r.title }}</label></span>
      }
      @if (!ctx.can('plan')) { <small class="muted">Su rol ({{ ctx.role() }}) puede modernizar, pero no aprobar cambios de contrato.</small> }
    </fieldset>
    <mf-action label="Modernizar candidato" icon="pi pi-sparkles" [disabledReason]="startReason()" [busy]="busy()" (run)="start()" />

    <mf-state [loading]="list.loading()" [error]="list.error()" [hasData]="!!list.data()" [empty]="list.data()?.length === 0"
              emptyText="Sin modernizaciones para esta ejecución." (retry)="list.reload()">
      @if (current(); as m) {
        <h3><mf-status [value]="m.state" /> Modernización {{ m.id.slice(0, 8) }} · {{ m.createdBy }}
          @if (m.approved.length) { · aprobado: <code>{{ m.approved.join(', ') }}</code> }</h3>
        @if (m.jobId && ['queued', 'running'].includes(m.state)) { <mf-job-log [jobId]="m.jobId" (finished)="list.reload()" /> }
        @if (m.job?.errorMessage) { <p-message severity="warn">{{ m.job?.errorCode }}: {{ m.job?.errorMessage }}</p-message> }
        @if (m.results.length) {
          <p-table [value]="m.results">
            <ng-template #header><tr><th scope="col">Regla</th><th scope="col">Tier</th><th scope="col">Confianza</th><th scope="col">Estado</th><th scope="col">Casos</th><th scope="col">Motivo</th></tr></ng-template>
            <ng-template #body let-r><tr>
              <td><strong>{{ r.rule }}</strong><br /><small>{{ r.title }}</small></td>
              <td><mf-light [classification]="tierLevel(r.tier)" /> <small>{{ r.tier }}</small></td><td>{{ r.confidence }}</td>
              <td><mf-status [value]="r.status" /></td><td>{{ r.items?.length ?? 0 }}</td><td><small>{{ r.reason }}</small></td></tr></ng-template>
          </p-table>
          <h3>Calidad antes / después</h3>
          <p-table [value]="quality(m)">
            <ng-template #header><tr><th scope="col">Métrica</th><th scope="col">Antes</th><th scope="col">Después</th></tr></ng-template>
            <ng-template #body let-q><tr><td>{{ q[0] }}</td><td>{{ q[1] }}</td><td>{{ q[2] }}</td></tr></ng-template>
          </p-table>
          <ul class="artifacts">@for (a of artifacts(m); track a[0]) {
            <li><button type="button" class="link" (click)="api.download(a[1], a[0])"><i class="pi pi-download"></i> {{ a[0] }}</button></li> }</ul>
        }
      }
    </mf-state>`,
  styles: [`.approvals{border:1px solid var(--p-content-border-color,#ddd);border-radius:6px;margin:.5rem 0;padding:.5rem .75rem}
    .check{display:flex;gap:.5rem;align-items:center;margin:.25rem 0}`],
})
export class ModernizationPanel {
  execution = input.required<Execution>();
  api = inject(Api);
  ctx = inject(ProjectContext);
  rules = new Loader<ModernizationRule[]>();
  list = new Loader<Modernization[]>();
  approve: string[] = [];
  busy = signal(false);
  error = signal<UiError | null>(null);
  current = computed(() => this.list.data()?.[0] ?? null);
  contractRules = computed(() => (this.rules.data() ?? []).filter(r => r.publicContract && r.mechanism !== 'proposal'));
  startReason = computed(() => {
    const e = this.execution();
    if (!this.ctx.can('execute')) return `Su rol (${this.ctx.role()}) no permite modernizar`;
    if (e.mode !== 'apply' || !['succeeded', 'partial_success'].includes(e.state)) return 'Requiere una ejecución terminada (no preview) con candidato';
    if (['queued', 'running'].includes(this.current()?.state ?? '')) return 'Ya hay una modernización en curso';
    return null;
  });

  constructor() {
    this.rules.load(() => this.api.modernizationRules());
    effect(() => { const id = this.execution().id; this.list.load(() => this.api.modernizations(id)); });
  }

  tierLevel(t: string) { return LEVEL[t] ?? 'REVIEW'; }

  async start() {
    this.error.set(null);
    this.busy.set(true);
    try { await this.api.startModernization(this.execution().id, this.approve); this.list.reload(); }
    catch (err) { this.error.set(UiError.from(err)); }
    finally { this.busy.set(false); }
  }

  quality(m: Modernization): [string, unknown, unknown][] {
    const b = m.qualityBefore as Record<string, any>, a = m.qualityAfter as Record<string, any>;
    const keys = ['build', 'javaFiles', 'javaLines', 'classes', 'routeBuilders', 'routes', 'testClasses', 'maxConfigureLines', 'findings'];
    const rows: [string, unknown, unknown][] = keys.map(k => [k, b?.[k] ?? '—', a?.[k] ?? '—']);
    const rules = new Set([...Object.keys(b?.['findingsByRule'] ?? {}), ...Object.keys(a?.['findingsByRule'] ?? {})]);
    rules.forEach(r => rows.push([`hallazgos ${r}`, b?.['findingsByRule']?.[r] ?? 0, a?.['findingsByRule']?.[r] ?? 0]));
    return rows;
  }

  artifacts(m: Modernization): [string, string][] {
    return Object.entries((m.summary as Record<string, any>)?.['artifacts'] ?? {}) as [string, string][];
  }
}

import { Component, computed, effect, inject, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MessageModule } from 'primeng/message';
import { SelectModule } from 'primeng/select';
import { TableModule } from 'primeng/table';
import { TextareaModule } from 'primeng/textarea';
import { Api } from '../../core/api';
import { UiError } from '../../core/errors';
import { Execution, Gate, Page, Validation } from '../../core/models';
import { ProjectContext } from '../../core/project-context';
import { Loader } from '../../shared/state';
import { ActionButton, PageHeader, StateView, StatusTag } from '../../shared/ui';
import { executionLabel } from '../execution/execution';

@Component({
  selector: 'mf-validation',
  imports: [FormsModule, MessageModule, SelectModule, TableModule, TextareaModule, ActionButton, PageHeader, StateView, StatusTag],
  template: `
    <mf-page-header title="Validación" crumb="VALIDACIÓN" subtitle="Gates, compilación, pruebas y evidencia de equivalencia" />
    <p-message severity="warn">Compilar y pasar pruebas unitarias no demuestra equivalencia funcional. G4 exige evidencia de la suite de
      equivalencia del proyecto (contratos, JMS, duplicados, redelivery, DLQ, rollback de BD). SKIPPED / NOT_RUN nunca cuentan como PASS.</p-message>
    <mf-state [loading]="list.loading()" [error]="list.error()" [hasData]="!!list.data()" [empty]="applied().length === 0"
              emptyText="Sin ejecuciones aplicadas. Ejecute un plan aprobado." (retry)="list.reload()">
      <div class="toolbar">
        <label for="ex">Ejecución</label>
        <p-select inputId="ex" [options]="applied()" optionValue="id" [ngModel]="selected()" (ngModelChange)="selected.set($event)" [style]="{ minWidth: '26rem' }">
          <ng-template #selectedItem let-e>{{ label(e) }}</ng-template><ng-template #item let-e>{{ label(e) }}</ng-template></p-select>
      </div>
      <mf-state [loading]="ex.loading()" [error]="ex.error()" [hasData]="!!ex.data()" (retry)="ex.reload()">
        @if (ex.data(); as e) {
          <h2>Gates</h2>
          <p-table [value]="gates()">
            <ng-template #header><tr><th scope="col">Gate</th><th scope="col">Descripción</th><th scope="col">Estado</th><th scope="col">Obligatorio</th><th scope="col">Detalle</th></tr></ng-template>
            <ng-template #body let-g><tr><td><strong>{{ g.gate }}</strong></td><td>{{ g.title }}</td><td><mf-status [value]="g.status" /></td>
              <td>{{ g.required ? 'Sí' : 'No' }}</td><td><small>{{ g.detail }}</small></td></tr></ng-template>
          </p-table>

          <h2>Suites ejecutadas</h2>
          <mf-state [loading]="vals.loading()" [error]="vals.error()" [hasData]="!!vals.data()" [empty]="vals.data()?.length === 0" emptyText="Sin validaciones registradas" (retry)="vals.reload()">
            <p-table [value]="vals.data() ?? []">
              <ng-template #header><tr><th scope="col">Objetivo</th><th scope="col">Suite</th><th scope="col">Gate</th><th scope="col">Estado</th>
                <th scope="col">Pruebas</th><th scope="col">Exit</th><th scope="col">Duración</th><th scope="col">Evidencia</th></tr></ng-template>
              <ng-template #body let-v><tr><td>{{ v.target === 'baseline' ? 'Línea base' : 'Candidato' }}</td><td>{{ v.suite }}</td><td>{{ v.gate }}</td>
                <td><mf-status [value]="v.status" /></td>
                <td>@if (v.tests) { {{ v.tests.tests }} total · {{ v.tests.failures }} fallos · {{ v.tests.errors }} errores · {{ v.tests.skipped }} omitidas }</td>
                <td>{{ v.exitCode ?? '—' }}</td><td>{{ v.durationMs ? (v.durationMs / 1000).toFixed(1) + ' s' : '—' }}</td>
                <td>@if (v.artifactId) { <button type="button" class="link" (click)="log(v)"><i class="pi pi-download"></i> log</button> }
                  @if (v.recordedBy) { <small>registrada por {{ v.recordedBy }}: {{ v.detail }}</small> } @else if (v.detail) { <small>{{ v.detail }}</small> }</td></tr></ng-template>
            </p-table>
          </mf-state>
          <mf-action label="Re-ejecutar suites" icon="pi pi-replay" severity="secondary" [outlined]="true"
                     disabledReason="Pendiente: re-ejecución de suites sobre el bundle del candidato aún no implementada (se ejecutan en cada ejecución)." />

          <h2>Registrar evidencia externa</h2>
          @if (evidenceReason(); as why) { <p class="muted"><i class="pi pi-info-circle" aria-hidden="true"></i> {{ why }}</p> }
          @else {
            <form class="form narrow" (ngSubmit)="record(e)">
              <label for="suite">Suite</label><p-select inputId="suite" name="suite" [options]="suites" optionLabel="l" optionValue="v" [(ngModel)]="ev.suite" />
              <label for="est">Resultado</label><p-select inputId="est" name="est" [options]="results" [(ngModel)]="ev.status" />
              <label for="det">Detalle y referencia a la evidencia (mínimo 10 caracteres)</label>
              <textarea pTextarea id="det" name="det" rows="3" [(ngModel)]="ev.detail"></textarea>
              @if (error(); as err) { <p-message severity="error">{{ err.code }}: {{ err.message }}</p-message> }
              <mf-action label="Registrar evidencia" icon="pi pi-save" [disabledReason]="ev.detail.trim().length < 10 ? 'Describa la evidencia' : null" (run)="record(e)" />
            </form>
          }

          @if (ctx.project()?.sourceType === 'git') {
          <h2>PR borrador</h2>
          @if (e.prUrl) { <p-message severity="success">PR borrador: <a [href]="e.prUrl" target="_blank" rel="noopener">{{ e.prUrl }}</a></p-message> }
          @else {
            @if (e.prBlockers.length) {
              <p-message severity="warn"><div><strong>No se puede crear el PR todavía:</strong><ul>@for (b of e.prBlockers; track b) { <li>{{ b }}</li> }</ul></div></p-message>
            }
            <mf-action label="Crear PR borrador" icon="pi pi-github" [disabledReason]="prReason()" (run)="pr(e)" />
            @if (prMsg()) { <p class="muted">{{ prMsg() }}</p> }
          }
          } @else {
            <p class="muted"><i class="pi pi-download" aria-hidden="true"></i> Origen local: el resultado se descarga como ZIP desde Ejecución → Resultado (no hay PR).</p>
          }

          <h2>Cómo validar equivalencia (fuera de la herramienta)</h2>
          <ul class="guide">
            <li><strong>Contratos</strong>: mismas entradas contra legacy y candidato; comparar códigos, headers, payload y esquemas (SOAP/XML/JSON).</li>
            <li><strong>JMS</strong>: mensaje correcto, duplicado, poison message, caída antes del ack, reconexión, orden por clave.</li>
            <li><strong>Redelivery y DLQ</strong>: número máximo de reentregas, destino DLQ y cabeceras de error idénticos.</li>
            <li><strong>Rollback de BD</strong>: excepción después de escribir; verificar atomicidad real (JTA/XA o compensación), no solo que arranca el contexto.</li>
          </ul>
          <p class="muted">Detalle en docs/16_EQUIVALENCIA.md.</p>
        }
      </mf-state>
    </mf-state>`,
})
export class ValidationPage {
  projectId = input.required<string>();
  ctx = inject(ProjectContext);
  private api = inject(Api);
  list = new Loader<Page<Execution>>();
  ex = new Loader<Execution>();
  vals = new Loader<Validation[]>();
  selected = signal<string | null>(null);
  error = signal<UiError | null>(null);
  prMsg = signal('');
  label = executionLabel;
  ev = { suite: 'equivalence', status: 'PASS', detail: '' };
  suites = [{ l: 'Equivalencia (G4)', v: 'equivalence' }, { l: 'Contratos (G3)', v: 'contracts' }];
  results = ['PASS', 'FAIL', 'SKIPPED'];
  applied = computed(() => (this.list.data()?.items ?? []).filter(e => e.mode === 'apply'));
  gates = computed(() => (this.ex.data()?.gates ?? []) as unknown as Gate[]);
  evidenceReason = computed(() => {
    const e = this.ex.data();
    if (!this.ctx.can('evidence')) return `Su rol (${this.ctx.role()}) no permite registrar evidencia`;
    if (!e || e.state !== 'succeeded' || e.rolledBack) return 'Requiere una ejecución terminada y vigente';
    return null;
  });
  prReason = computed(() => {
    const e = this.ex.data();
    if (!this.ctx.can('pull_request')) return `Su rol (${this.ctx.role()}) no permite crear PR`;
    if (this.ctx.project()?.sourceType !== 'git') return 'Ramas y PR solo existen para orígenes Git; descargue candidate.zip en Ejecución';
    if (e?.prBlockers.length) return 'Bloqueado por gates o configuración (ver lista)';
    return null;
  });

  constructor() {
    effect(async () => {
      const id = this.projectId();
      this.ctx.select(id);
      const page = await this.list.load(() => this.api.executions(id, { limit: 50 }));
      this.selected.set(page?.items.find(e => e.mode === 'apply')?.id ?? null);
    });
    effect(() => { const id = this.selected(); if (id) this.load(id); });
  }

  load(id: string) { this.ex.load(() => this.api.execution(id)); this.vals.load(() => this.api.validations(id)); }
  log(v: Validation) { this.api.download(v.artifactId!, `${v.target}-${v.suite}.log`); }

  async record(e: Execution) {
    this.error.set(null);
    try {
      await this.api.recordEvidence(e.id, { ...this.ev, detail: this.ev.detail.trim() });
      this.ev.detail = '';
      this.load(e.id);
    } catch (err) {
      this.error.set(UiError.from(err));
    }
  }

  async pr(e: Execution) {
    try {
      await this.api.pullRequest(e.id);
      this.prMsg.set('Creación del PR en cola; consulte Ejecución para el progreso.');
    } catch (err) {
      const u = UiError.from(err);
      this.prMsg.set(`${u.code}: ${u.message}`);
    }
  }
}

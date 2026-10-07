import { Component, computed, effect, inject, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ConfirmationService } from 'primeng/api';
import { ConfirmDialogModule } from 'primeng/confirmdialog';
import { MessageModule } from 'primeng/message';
import { SelectModule } from 'primeng/select';
import { TableModule } from 'primeng/table';
import { Api } from '../../core/api';
import { UiError } from '../../core/errors';
import { Artifact, Execution, ExecutionStep, Page } from '../../core/models';
import { ProjectContext } from '../../core/project-context';
import { Loader } from '../../shared/state';
import { ActionButton, JobLog, PageHeader, StateView, StatusTag, short } from '../../shared/ui';
import { ChangesPanel } from './changes';
import { ModernizationPanel } from './modernization';
import { ResultPanel } from './result';

/** Shared execution selector used by Ejecución, Diff and Validación. */
export function executionLabel(e: Execution) {
  return `${new Date(e.createdAt).toLocaleString()} · ${e.mode === 'apply' ? 'ejecución' : 'preview'} · ${e.state}`;
}

@Component({
  selector: 'mf-execution',
  imports: [FormsModule, ConfirmDialogModule, MessageModule, SelectModule, TableModule, ActionButton, JobLog, PageHeader, StateView, StatusTag, ChangesPanel, ModernizationPanel, ResultPanel],
  providers: [ConfirmationService],
  template: `
    <p-confirmdialog />
    <mf-page-header title="Ejecución" crumb="EJECUCIÓN" subtitle="Candidato generado en una copia aislada del repositorio" />
    <mf-state [loading]="list.loading()" [error]="list.error()" [hasData]="!!list.data()" [empty]="list.data()?.items?.length === 0"
              emptyText="Sin ejecuciones. Genere un preview o ejecute un plan aprobado desde Plan." (retry)="list.reload()">
      <div class="toolbar">
        <label for="ex">Ejecución</label>
        <p-select inputId="ex" [options]="list.data()?.items ?? []" optionValue="id" [ngModel]="selected()" (ngModelChange)="selected.set($event)" [style]="{ minWidth: '26rem' }">
          <ng-template #selectedItem let-e>{{ label(e) }}</ng-template><ng-template #item let-e>{{ label(e) }}</ng-template></p-select>
      </div>
      @if (error(); as e) { <p-message severity="error">{{ e.code }}: {{ e.message }}</p-message> }
      <mf-state [loading]="ex.loading()" [error]="ex.error()" [hasData]="!!ex.data()" (retry)="ex.reload()">
        @if (ex.data(); as e) {
          <div class="plan-head">
            <span><mf-status [value]="e.state" /> {{ e.mode === 'apply' ? 'Ejecución' : 'Preview (dryRun)' }} · snapshot base <code>{{ short(e.baseSnapshot) }}</code>
              @if (e.baseSha) { (commit <code>{{ short(e.baseSha) }}</code>) } · por {{ e.createdBy }}</span>
            <span class="actions">
              <mf-action label="Cancelar" icon="pi pi-stop" severity="danger" [outlined]="true" [disabledReason]="cancelReason()" (run)="cancel(e)" />
              <mf-action label="Descartar candidato (rollback)" icon="pi pi-undo" severity="warn" [outlined]="true" [disabledReason]="rollbackReason()" (run)="confirmRollback(e)" />
              <mf-action label="Reintentar fase" icon="pi pi-replay" severity="secondary" [outlined]="true"
                         disabledReason="Los reintentos técnicos son automáticos (lease); el reintento manual por fase aún no está implementado." />
            </span>
          </div>
          @if (e.summary['status']) {
            <p-message [severity]="e.summary['status'] === 'SUCCEEDED' ? 'success' : e.summary['status'] === 'PARTIAL_SUCCESS' ? 'warn' : 'error'">
              <span><strong>Migration status: {{ e.summary['status'] }}</strong>
                @if (e.summary['primaryReason']) { · motivo principal: <code>{{ e.summary['primaryReason'] }}</code> }
                @if ($any(e.summary['reasonCodes'])?.length) { · códigos: {{ $any(e.summary['reasonCodes']).join(', ') }} }</span>
            </p-message>
          }
          @if ($any(e.summary['baselineDependencies'])?.length) {
            <p-message [severity]="e.summary['migrationMode'] === 'DEGRADED' ? 'warn' : 'error'">
              <div><strong>Baseline: {{ e.summary['baseline'] === 'BASELINE_BLOCKED_INTERNAL_ARTIFACT' ? 'bloqueado por artefacto interno' : 'bloqueado por dependencia externa' }}</strong>
                ({{ e.summary['baseline'] }})
                <ul>@for (d of $any(e.summary['baselineDependencies']); track d.artifact) {
                  <li><code>{{ d.artifact }}</code> — {{ d.reason }} · {{ d.dependencyCategory }} (impacto {{ d.migrationCriticality }})@if (d.hint) { <br /><small>{{ d.hint }}</small> }</li> }</ul>
                Código: <strong>no validado</strong> (Maven no llegó a compilar) · Análisis: continuado parcialmente ·
                Migración: <strong>{{ e.summary['migrationMode'] === 'DEGRADED' ? 'modo degradado' : 'bloqueada' }}</strong>
                @if (e.summary['validationConfidence']) { · Confianza de validación: {{ e.summary['validationConfidence'] }} }
                <br /><small>Acción requerida: proveer el artefacto o el repositorio aprobado. No se reemplaza la dependencia automáticamente.</small></div>
            </p-message>
          }
          @if ($any(e.summary['platform']); as pl) {
            <p-message [severity]="pl.primaryReason ? 'error' : 'info'">
              <div><strong>Plataforma: {{ pl.platform === 'RED_HAT_FUSE_KARAF' ? 'Red Hat Fuse / Karaf' : pl.platform }}</strong>
                <ul>@for (b of pl.vendorBoms; track b.artifactId + b.file) {
                  <li>BOM de proveedor @if (b.status === 'UNRESOLVED') { <strong>⚠ no disponible</strong> } @else { {{ b.status }} }:
                    <code>{{ b.groupId }}:{{ b.artifactId }}:{{ b.version }}</code></li> }</ul>
                @if (pl.primaryReason) {
                  Reparación de baseline: <strong>{{ pl.repair?.status === 'PARTIAL' ? 'parcial' : pl.repair?.status }}</strong>
                  · dependencias Camel recuperadas: {{ pl.repair?.recovered ?? 0 }} · aún sin versión: {{ pl.repair?.stillUnresolved?.length ?? 0 }}
                  <br />Análisis estático: ✓ disponible · Migración automática: <strong>bloqueada</strong> · Compilación: no ejecutable
                  <br /><small>Acción requerida: {{ pl.requiredAction }}</small>
                }
                @if (pl.runtimeMigration === 'REQUIRED') { <br /><small>Migración de runtime requerida (OSGi/Karaf → Spring Boot 3 / Quarkus 3).</small> }</div>
            </p-message>
          }
          @if (e.summary['note']) { <p-message severity="info">{{ e.summary['note'] }}</p-message> }
          @if (e.summary['sourceUnchanged']) { <p class="muted"><i class="pi pi-shield" aria-hidden="true"></i> El origen (carpeta, ZIP o Git) no se modifica: el candidato se entrega como candidate.zip, con candidate.patch y reportes.</p> }
          @if (e.job?.errorMessage) { <p-message severity="error">{{ e.job?.errorCode }}: {{ e.job?.errorMessage }}</p-message> }

          <h2>Pasos</h2>
          <p-table [value]="steps()">
            <ng-template #header><tr><th scope="col">Paso</th><th scope="col">Estado</th><th scope="col">Commit</th><th scope="col">Recetas</th><th scope="col">Duración</th><th scope="col">Log</th><th scope="col">Motivo</th></tr></ng-template>
            <ng-template #body let-s>
              <tr><td><strong>{{ s.title }}</strong><br /><small class="mono muted">{{ s.key }}</small></td><td><mf-status [value]="s.state" /></td>
                <td class="mono">{{ s.commit ? s.commit.slice(0, 10) : '—' }}</td><td class="mono small">{{ (s.recipes ?? []).join(', ') }}</td>
                <td>{{ s.durationMs ? (s.durationMs / 1000).toFixed(1) + ' s' : '—' }}</td>
                <td>@if (s.logArtifactId) { <button type="button" class="link" (click)="downloadId(s.logArtifactId, 'rewrite-' + s.key + '.log')"><i class="pi pi-download"></i> log</button> }</td>
                <td><small>{{ (s.reasons ?? []).join('; ') }}</small></td></tr>
            </ng-template>
          </p-table>

          @if (e.mode === 'apply' && !['queued', 'running'].includes(e.state)) {
            <h2>Resultado</h2>
            <mf-result [execution]="e" />
          }
          @if (e.mode === 'apply') {
            <h2>Registro de cambios</h2>
            <mf-changes [executionId]="e.id" />
            <h2>Modernización</h2>
            <mf-modernization [execution]="e" />
          }

          @if (e.jobId) { <h2>Progreso y logs (redactados)</h2><mf-job-log [jobId]="e.jobId" (finished)="refresh()" /> }

          <h2>Artefactos</h2>
          <mf-state [loading]="artifacts.loading()" [error]="artifacts.error()" [hasData]="!!artifacts.data()" [empty]="artifacts.data()?.items?.length === 0"
                    emptyText="Aún sin artefactos" (retry)="artifacts.reload()">
            <ul class="artifacts">@for (a of artifacts.data()?.items ?? []; track a.id) {
              <li><button type="button" class="link" (click)="download(a)"><i class="pi pi-download"></i> {{ a.name }}</button>
                <small class="muted">{{ a.kind }} · {{ a.sizeBytes }} B · sha256 {{ a.sha256.slice(0, 12) }}…</small></li> }</ul>
          </mf-state>
        }
      </mf-state>
    </mf-state>`,
})
export class ExecutionPage {
  projectId = input.required<string>();
  executionId = input<string | undefined>();
  ctx = inject(ProjectContext);
  private api = inject(Api);
  private confirm = inject(ConfirmationService);
  list = new Loader<Page<Execution>>();
  ex = new Loader<Execution>();
  artifacts = new Loader<Page<Artifact>>();
  selected = signal<string | null>(null);
  error = signal<UiError | null>(null);
  short = short;
  label = executionLabel;
  steps = computed(() => (this.ex.data()?.steps ?? []) as unknown as ExecutionStep[]);
  cancelReason = computed(() => {
    const e = this.ex.data();
    if (!this.ctx.can('execute')) return `Su rol (${this.ctx.role()}) no permite cancelar`;
    if (e?.job?.cancelRequested && e.state === 'running') return 'Cancelación solicitada; esperando confirmación del worker';
    return e && ['queued', 'running'].includes(e.state) ? null : 'La ejecución no está en curso';
  });
  rollbackReason = computed(() => {
    const e = this.ex.data();
    if (!this.ctx.can('execute')) return `Su rol (${this.ctx.role()}) no permite descartar`;
    if (!e) return 'Sin ejecución';
    if (['queued', 'running'].includes(e.state)) return 'Cancele antes de descartar';
    if (e.rolledBack) return 'El candidato ya fue descartado';
    if (e.prUrl) return 'Ya existe un PR; ciérrelo en el proveedor Git';
    return null;
  });

  constructor() {
    effect(async () => {
      const id = this.projectId();
      this.ctx.select(id);
      const page = await this.list.load(() => this.api.executions(id, { limit: 50 }));
      this.selected.set(this.executionId() ?? page?.items[0]?.id ?? null);
    });
    effect(() => { const id = this.selected(); if (id) this.loadExecution(id); });
  }

  loadExecution(id: string) {
    this.ex.load(() => this.api.execution(id));
    this.artifacts.load(() => this.api.artifacts(this.projectId(), { executionId: id, limit: 100 }));
  }

  refresh() { const id = this.selected(); if (id) this.loadExecution(id); this.list.reload(); }

  async cancel(e: Execution) {
    this.error.set(null);
    try { await this.api.cancelExecution(e.id); } catch (err) { this.error.set(UiError.from(err)); }
    this.refresh();
  }

  confirmRollback(e: Execution) {
    this.confirm.confirm({
      header: 'Descartar candidato',
      message: 'El candidato quedará inválido para PR. El repositorio origen no se modifica y los artefactos se conservan como evidencia. ¿Continuar?',
      acceptLabel: 'Descartar', rejectLabel: 'Cancelar',
      accept: async () => {
        try { await this.api.rollback(e.id); } catch (err) { this.error.set(UiError.from(err)); }
        this.refresh();
      },
    });
  }

  download(a: Artifact) { this.api.download(a.id, a.name); }
  downloadId(id: string, name: string) { this.api.download(id, name); }
}

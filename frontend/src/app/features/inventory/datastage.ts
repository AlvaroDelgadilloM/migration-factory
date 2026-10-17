import { Component, computed, effect, inject, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { InputTextModule } from 'primeng/inputtext';
import { MessageModule } from 'primeng/message';
import { TableModule } from 'primeng/table';
import { TabsModule } from 'primeng/tabs';
import { Api } from '../../core/api';
import { UiError } from '../../core/errors';
import { DataStageMigration } from '../../core/models';
import { ProjectContext } from '../../core/project-context';
import { Loader } from '../../shared/state';
import { ActionButton, JobLog, StateView, StatusTag } from '../../shared/ui';

interface JobRow { name: string; jobType: string; complexity: string; score: number; migratability: string; confidence: string;
  state: string; dependsOn: string[]; manualActions: string[]; dag: string | null; reasons: string[] }

/** Doc 63: DataStage → Airflow project. Inventory, classification, waves, gates and the generated Airflow project. */
@Component({
  selector: 'mf-datastage',
  imports: [FormsModule, InputTextModule, MessageModule, TableModule, TabsModule, ActionButton, JobLog, StateView, StatusTag],
  template: `
    <p class="muted">Tipo de migración <strong>IBM DataStage → Apache Airflow</strong>. Se leen las exportaciones DSX (nunca se ejecuta su
      contenido), se generan DAGs, SQL para Oracle y configuración sin secretos, y se validan con DagBag. La equivalencia funcional
      requiere la reconciliación contra la salida de DataStage: hasta entonces el resultado no es <code>SUCCEEDED</code>.</p>
    <div class="form">
      <span><label for="tz">Zona horaria de los DAGs</label><input pInputText id="tz" [(ngModel)]="timezone" /></span>
      <mf-action label="Analizar y migrar" icon="pi pi-play" [busy]="busy()" [disabledReason]="reason()" (run)="start()" />
    </div>
    @if (error(); as e) { <p-message severity="error">{{ e.code }}: {{ e.message }}</p-message> }

    <mf-state [loading]="list.loading()" [error]="list.error()" [hasData]="!!list.data()" [empty]="list.data()?.length === 0"
              emptyText="Aún no se ha migrado. Use «Analizar y migrar»." (retry)="list.reload()">
      @if (current(); as m) {
        @if (m.state === 'queued' || m.state === 'running') {
          <h3>Migración en curso</h3>
          <mf-job-log [jobId]="m.jobId!" (finished)="list.reload()" />
        } @else if (summary(); as s) {
          <p>Estado <mf-status [value]="s.status" /> · validación técnica <strong>{{ s.technicalValidation }}</strong> ·
            funcional <strong>{{ s.functionalValidation }}</strong> · DataStage {{ s.parse?.dataStageVersion }} · Airflow {{ s.airflow?.airflow }}</p>
          <div class="cards">
            <article><b>{{ s.parse?.jobs }}</b> jobs ({{ s.parse?.sequences }} secuencias) en {{ s.parse?.files }} DSX</article>
            <article><b>{{ s.counts?.AUTO_MIGRATED ?? 0 }}</b> AUTO_MIGRATED</article>
            <article><b>{{ s.counts?.SEMIAUTOMATIC ?? 0 }}</b> SEMIAUTOMATIC</article>
            <article><b>{{ s.counts?.MANUAL_REVIEW ?? 0 }}</b> MANUAL_REVIEW</article>
            <article><b>{{ (s.counts?.BLOCKED ?? 0) + (s.counts?.FAILED ?? 0) }}</b> BLOCKED / FAILED</article>
            <article><b>{{ s.dags?.length ?? 0 }}</b> DAGs generados</article>
          </div>
          <ul class="artifacts">@for (a of artifacts(); track a[0]) {
            <li><button type="button" class="link" (click)="api.download(a[1], a[0])"><i class="pi pi-download"></i> {{ a[0] }}</button></li> }</ul>
          <p-tabs value="jobs">
            <p-tablist><p-tab value="jobs">Jobs</p-tab><p-tab value="gates">Gates</p-tab><p-tab value="waves">Oleadas</p-tab>
              <p-tab value="manual">Acciones manuales ({{ s.manualActions?.length ?? 0 }})</p-tab></p-tablist>
            <p-tabpanels>
              <p-tabpanel value="jobs">
                <p-table [value]="rows()" dataKey="name" [expandedRowKeys]="expanded" sortField="name">
                  <ng-template #header><tr><th></th><th scope="col">Job</th><th scope="col">Tipo</th><th scope="col">Complejidad</th>
                    <th scope="col">Migrabilidad</th><th scope="col">Confianza</th><th scope="col">Resultado</th><th scope="col">DAG</th></tr></ng-template>
                  <ng-template #body let-r let-open="expanded">
                    <tr>
                      <td><button type="button" class="link" [attr.aria-label]="'Detalle de ' + r.name" (click)="toggle(r.name)">
                        <i [class]="open ? 'pi pi-chevron-down' : 'pi pi-chevron-right'"></i></button></td>
                      <td class="mono">{{ r.name }}@if (pilot()[r.name]) { <span class="chip">piloto {{ pilot()[r.name] }}</span> }</td>
                      <td>{{ r.jobType }}</td><td>{{ r.complexity }} ({{ r.score }})</td><td>{{ r.migratability }}</td><td>{{ r.confidence }}</td>
                      <td><mf-status [value]="r.state" /></td><td class="mono small">{{ r.dag ?? '—' }}</td>
                    </tr>
                  </ng-template>
                  <ng-template #expandedrow let-r>
                    <tr><td colspan="8">
                      <p class="small"><strong>Factores:</strong> {{ r.reasons.join(' · ') }}</p>
                      @if (r.dependsOn.length) { <p class="small"><strong>Depende de:</strong> {{ r.dependsOn.join(', ') }}</p> }
                      @if (r.manualActions.length) { <ul class="small">@for (a of r.manualActions; track a) { <li>{{ a }}</li> }</ul> }
                    </td></tr>
                  </ng-template>
                </p-table>
              </p-tabpanel>
              <p-tabpanel value="gates">
                <p-table [value]="s.gates ?? []">
                  <ng-template #header><tr><th scope="col">Gate</th><th scope="col">Estado</th><th scope="col">Detalle</th></tr></ng-template>
                  <ng-template #body let-g><tr><td class="mono">{{ g.gate }}</td><td><mf-status [value]="g.status" /></td><td>{{ g.detail }}</td></tr></ng-template>
                </p-table>
              </p-tabpanel>
              <p-tabpanel value="waves">
                <ol class="waves">@for (w of s.waves ?? []; track w.wave) { <li><span class="mono small">{{ w.jobs.join(', ') }}</span></li> }</ol>
                @if (s.droppedEdges?.length) {
                  <p class="small muted">Dependencias de datos descartadas porque contradecían el orden explícito de una secuencia:</p>
                  <ul class="small">@for (d of s.droppedEdges; track $index) { <li>{{ d.from }} → {{ d.to }} ({{ d.resource }})</li> }</ul>
                }
              </p-tabpanel>
              <p-tabpanel value="manual">
                <ul class="small">@for (a of s.manualActions ?? []; track a) { <li>{{ a }}</li> }</ul>
              </p-tabpanel>
            </p-tabpanels>
          </p-tabs>
        } @else {
          <p-message severity="error">La migración terminó en estado «{{ m.state }}»: {{ m.job?.errorMessage }}</p-message>
        }
      }
    </mf-state>`,
  styles: [`.form{display:flex;gap:1rem;align-items:flex-end;flex-wrap:wrap;margin:.5rem 0}.form span{display:flex;flex-direction:column;gap:.25rem}
    .cards{display:flex;gap:.75rem;flex-wrap:wrap;margin:.5rem 0}.cards article{border:1px solid var(--p-content-border-color,#ddd);border-radius:6px;padding:.5rem .75rem}
    .artifacts{display:flex;gap:1rem;flex-wrap:wrap;list-style:none;padding:0}.waves li{margin:.25rem 0}.chip{margin-left:.5rem}`],
})
export class DataStagePanel {
  projectId = input.required<string>();
  api = inject(Api);
  ctx = inject(ProjectContext);
  list = new Loader<DataStageMigration[]>();
  timezone = 'America/Mexico_City';
  busy = signal(false);
  error = signal<UiError | null>(null);
  expanded: Record<string, boolean> = {};
  current = computed(() => this.list.data()?.[0] ?? null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any -- free-form report written by the engine
  summary = computed<any>(() => this.current()?.summary ?? null);
  rows = computed<JobRow[]>(() => Object.entries((this.current()?.results ?? {}) as Record<string, any>).map(([name, r]) => ({
    name, jobType: r.jobType, complexity: r.complexity, score: r.score, migratability: r.migratability, confidence: r.confidence,
    state: r.state, dependsOn: r.dependsOn ?? [], manualActions: r.manualActions ?? [], dag: r.dag ?? null, reasons: r.reasons ?? [] })));
  pilot = computed(() => Object.fromEntries(Object.entries((this.summary()?.['pilot'] ?? {}) as Record<string, string>).map(([k, v]) => [v, k])));
  artifacts = computed(() => Object.entries((this.summary()?.['artifacts'] ?? {}) as Record<string, string>)
    .map(([k, v]) => [k.replace(/^ds-[0-9a-f]+\./, ''), v] as [string, string]));
  reason = computed(() => {
    if (!this.ctx.can('execute')) return `Su rol (${this.ctx.role()}) no permite migrar`;
    if (['queued', 'running'].includes(this.current()?.state ?? '')) return 'Ya hay una migración en curso';
    return null;
  });

  constructor() {
    effect(() => { const id = this.projectId(); this.list.load(() => this.api.dataStageMigrations(id)); });
  }

  toggle(name: string) { this.expanded = { ...this.expanded, [name]: !this.expanded[name] }; }

  async start() {
    this.error.set(null);
    this.busy.set(true);
    try { await this.api.startDataStageMigration(this.projectId(), { timezone: this.timezone.trim() }); this.list.reload(); }
    catch (e) { this.error.set(UiError.from(e)); }
    finally { this.busy.set(false); }
  }
}

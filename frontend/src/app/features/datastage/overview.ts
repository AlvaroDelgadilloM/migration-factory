import { Component, computed, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { ButtonModule } from 'primeng/button';
import { InputTextModule } from 'primeng/inputtext';
import { MessageModule } from 'primeng/message';
import { ProgressBarModule } from 'primeng/progressbar';
import { TableModule } from 'primeng/table';
import { Api } from '../../core/api';
import { ActionButton, JobLog, PageHeader, StateView, StatusTag, STATUS_HINT } from '../../shared/ui';
import { DataStageStore } from './store';

/** DataStage → Airflow · Resumen: status in plain words, the next step, the gates and the downloads. */
@Component({
  selector: 'mf-ds-overview',
  imports: [FormsModule, RouterLink, ButtonModule, InputTextModule, MessageModule, ProgressBarModule, TableModule, ActionButton, JobLog,
            PageHeader, StateView, StatusTag],
  template: `
    <mf-page-header title="Resumen" crumb="DATASTAGE → AIRFLOW" subtitle="Dónde está la migración y qué sigue.">
      <span class="tz"><label for="tz">Zona horaria de los DAGs</label><input pInputText id="tz" [(ngModel)]="timezone" /></span>
      <mf-action [label]="ds.current() ? 'Volver a migrar' : 'Analizar y migrar'" icon="pi pi-play" [busy]="ds.busy()"
                 [disabledReason]="ds.startReason()" (run)="ds.start(timezone)" />
    </mf-page-header>
    @if (ds.error(); as e) { <p-message severity="error">{{ e.code }}: {{ e.message }}</p-message> }

    <mf-state [loading]="ds.list.loading()" [error]="ds.list.error()" [hasData]="!!ds.list.data()" [empty]="ds.list.data()?.length === 0"
              emptyText="Aún no se ha migrado. «Analizar y migrar» lee los DSX, genera el proyecto Airflow y lo valida." (retry)="ds.reload()">
      @if (ds.current(); as m) {
        @if (ds.running()) {
          <section class="card"><h2>Migración en curso</h2><mf-job-log [jobId]="m.jobId!" (finished)="ds.reload()" /></section>
        } @else if (s(); as s) {
          <section class="hero">
            <div>
              <mf-status [value]="s.status" />
              <h2>{{ headline() }}</h2>
              <p class="muted">{{ hint(s.status) }} DataStage {{ s.parse?.dataStageVersion }} → Airflow {{ s.airflow?.airflow }}.</p>
            </div>
            @if (ds.airflowZip(); as z) {
              <p-button label="Descargar proyecto Airflow" icon="pi pi-download" size="large" styleClass="nowrap-btn" (onClick)="api.download(z.id, z.name)" />
            }
          </section>

          <section class="next">
            <h3><i class="pi pi-flag" aria-hidden="true"></i> Siguiente paso</h3>
            @if (pending() > 0) {
              <p>Resolver <strong>{{ pending() }}</strong> acción(es) manual(es): los pasos que las tienen fallan a propósito hasta completarlas.</p>
              <a pButton [routerLink]="['/p', ds.ctx.projectId(), 'ds', 'acciones']" label="Ir a acciones manuales" icon="pi pi-arrow-right" iconPos="right" [outlined]="true"></a>
            } @else if (s.functionalValidation !== 'FUNCTIONALLY_VALIDATED') {
              <p>Comparar los datos: correr DataStage y Airflow con la misma entrada y ejecutar <code>tests/reconciliation/</code> del proyecto.</p>
            } @else { <p>Listo para revisión del equipo y despliegue por su proceso habitual.</p> }
            <p-progressbar [value]="progress()" [showValue]="false" aria-label="Acciones manuales resueltas" />
            <small class="muted">{{ doneCount() }} de {{ ds.actions().length }} acciones resueltas (incluye las de revisión)</small>
          </section>

          <div class="cards">
            <article><i class="pi pi-database" aria-hidden="true"></i><b>{{ s.parse?.jobs }}</b> jobs · {{ s.parse?.sequences }} secuencias</article>
            <article class="ok-c"><i class="pi pi-bolt" aria-hidden="true"></i><b>{{ s.counts?.AUTO_MIGRATED ?? 0 }}</b> automáticos</article>
            <article class="info-c"><i class="pi pi-eye" aria-hidden="true"></i><b>{{ s.counts?.SEMIAUTOMATIC ?? 0 }}</b> para revisar</article>
            <article class="warn-c"><i class="pi pi-user-edit" aria-hidden="true"></i><b>{{ s.counts?.MANUAL_REVIEW ?? 0 }}</b> con trabajo manual</article>
            <article><i class="pi pi-share-alt" aria-hidden="true"></i><b>{{ s.dags?.length ?? 0 }}</b> DAGs generados</article>
          </div>

          <div class="grid">
            <section class="card">
              <h3>Controles de calidad</h3>
              <p-table [value]="s.gates ?? []" size="small">
                <ng-template #body let-g><tr><td>{{ gateName(g.gate) }}</td><td><mf-status [value]="g.status" /></td><td class="small">{{ g.detail }}</td></tr></ng-template>
              </p-table>
            </section>
            <section class="card">
              <h3>Piloto recomendado</h3>
              <ul class="pilot">@for (p of pilot(); track p[0]) { <li><span class="chip">{{ p[0] }}</span> <span class="mono small">{{ p[1] }}</span></li> }</ul>
              <h3>Reportes</h3>
              <ul class="artifacts">@for (a of ds.artifacts(); track a.id) {
                <li><button type="button" class="link" (click)="api.download(a.id, a.name)"><i class="pi pi-file" aria-hidden="true"></i> {{ a.name }}</button></li> }</ul>
            </section>
          </div>
        } @else {
          <p-message severity="error">La migración terminó en estado «{{ m.state }}»: {{ m.job?.errorMessage }}</p-message>
        }
      }
    </mf-state>`,
  styles: [`.tz{display:flex;flex-direction:column;gap:4px}.tz label{font-size:12px;color:var(--mf-muted)}
    .hero{display:flex;justify-content:space-between;align-items:center;gap:1rem;background:#fff;border:1px solid var(--mf-line);border-radius:12px;padding:20px 24px;margin-bottom:16px}
    .hero h2{margin:8px 0 4px;font-size:22px}
    .next{background:#f0f7f7;border:1px solid #cfe5e6;border-radius:12px;padding:16px 20px;margin-bottom:16px}.next h3{margin:0 0 6px}
    .next p-progressbar{display:block;margin:10px 0 4px;height:8px}
    .grid{display:grid;grid-template-columns:minmax(0,3fr) minmax(0,2fr);gap:16px}.card{background:#fff;border:1px solid var(--mf-line);border-radius:12px;padding:16px 20px}
    .card h3{margin:0 0 10px}.pilot{list-style:none;padding:0;margin:0 0 14px}.pilot li{padding:4px 0;display:flex;gap:8px;align-items:baseline}
    .pilot .mono{word-break:break-all}:host ::ng-deep .nowrap-btn{white-space:nowrap}.hero{flex-wrap:wrap}
    .ok-c b{color:#067647}.info-c b{color:#0a5f73}.warn-c b{color:#b54708}
    .ok-c > i{background:#e7f6ec !important;color:#067647 !important}.warn-c > i{background:#fff4e5 !important;color:#b54708 !important}
    @media (max-width:1100px){.grid{grid-template-columns:1fr}}`],
})
export class DsOverview {
  ds = inject(DataStageStore);
  api = inject(Api);
  timezone = 'America/Mexico_City';
  s = this.ds.summary;
  doneCount = computed(() => this.ds.actions().filter(a => this.ds.actionState()[a.key]?.done).length);
  pending = computed(() => this.ds.actions().filter(a => a.severity === 'manual' && !this.ds.actionState()[a.key]?.done).length);
  progress = computed(() => this.ds.actions().length ? Math.round(100 * this.doneCount() / this.ds.actions().length) : 0);
  pilot = computed(() => Object.entries((this.s()?.pilot ?? {}) as Record<string, string>)
    .map(([k, v]) => [{ LOW: 'Baja', MEDIUM: 'Media', HIGH: 'Alta', MASTER_SEQUENCE: 'Secuencia maestra' }[k] ?? k, v]));
  headline = computed(() => {
    const c = this.s()?.counts ?? {};
    const total = Object.values(c).reduce((a: number, b) => a + Number(b), 0);
    return `${(c.AUTO_MIGRATED ?? 0) + (c.SEMIAUTOMATIC ?? 0)} de ${total} jobs generados y ejecutables · ${c.MANUAL_REVIEW ?? 0} requieren trabajo manual`;
  });
  hint(code: string) { return STATUS_HINT[code] ?? ''; }
  gateName(g: string) {
    return ({ DSX_PARSE_GATE: 'Lectura de los DSX', DEPENDENCY_GRAPH_GATE: 'Grafo de dependencias', STAGE_MAPPING_GATE: 'Mapeo de etapas',
      SECRET_GATE: 'Secretos', CONNECTION_MANIFEST_GATE: 'Conexiones sin contraseñas', DAG_PARSE_GATE: 'DAGs importan en Airflow',
      SQL_VALIDATION_GATE: 'Estructura del SQL', RECONCILIATION_GATE: 'Reconciliación de datos', PACKAGE_GATE: 'Paquete ZIP' } as Record<string, string>)[g] ?? g;
  }
}

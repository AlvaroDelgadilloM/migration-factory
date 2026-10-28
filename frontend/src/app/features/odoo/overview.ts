import { Component, computed, inject } from '@angular/core';
import { RouterLink } from '@angular/router';
import { MessageModule } from 'primeng/message';
import { TableModule } from 'primeng/table';
import { Api } from '../../core/api';
import { ActionButton, JobLog, PageHeader, StateView, StatusTag } from '../../shared/ui';
import { OdooStore } from './store';

const HOP_STATE: Record<string, string> = { succeeded: 'Listo', failed: 'Falló', cancelled: 'Cancelado', timed_out: 'Sin ejecutor', running: 'En curso' };

/** Odoo 12 -> 19 · Resumen: analyze the uploaded database, then migrate it hop by hop and reconcile before/after. */
@Component({
  selector: 'mf-odoo-overview',
  imports: [RouterLink, MessageModule, TableModule, ActionButton, JobLog, PageHeader, StateView, StatusTag],
  styles: [`
    .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(9rem, 1fr)); gap: 10px; margin: 10px 0 16px; }
    .cards article { background: #fff; border: 1px solid var(--mf-line); border-radius: 12px; padding: 10px 14px; }
    .cards b { display: block; font-size: 22px; } .card { background: #fff; border: 1px solid var(--mf-line); border-radius: 12px; padding: 14px 18px; margin: 0 0 16px; }
    .hops { display: grid; grid-template-columns: repeat(auto-fit, minmax(8rem, 1fr)); gap: 8px; margin: 8px 0; }
    .hop { border: 1px solid var(--mf-line); border-radius: 10px; padding: 8px 10px; font-size: 13px; }
    .hop b { display: block; font-size: 15px; } .hop.succeeded { border-color: #12b76a; } .hop.failed, .hop.timed_out { border-color: #d92d20; }
    pre { background: #0f1720; color: #e2e8f0; padding: 10px; border-radius: 8px; font-size: 12px; max-height: 22rem; overflow: auto; white-space: pre-wrap; }
    .bad { color: #b42318; } .ok { color: #067647; } h2 { font-size: 17px; margin: 0 0 8px; }`],
  template: `
    <mf-page-header title="Resumen" crumb="ODOO 12 → 19" subtitle="Base de datos de Odoo Community migrada con OpenUpgrade, salto por salto, sobre una copia.">
      <mf-action label="Analizar la base" icon="pi pi-search" [outlined]="!!os.analysis()" [busy]="os.busy()" [disabledReason]="analyzeReason()" (run)="os.start('analyze')" />
      <mf-action label="Migrar a Odoo 19" icon="pi pi-play" [busy]="os.busy()" [disabledReason]="migrateReason()" (run)="os.start('migrate')" />
    </mf-page-header>
    @if (os.error(); as e) { <p-message severity="error">{{ e.code }}: {{ e.message }}</p-message> }
    <mf-state [loading]="os.list.loading()" [error]="os.list.error()" [hasData]="!!os.list.data()" [empty]="os.list.data()?.length === 0"
              emptyText="Aún no se analiza la base. «Analizar la base» la restaura en un servidor aislado y revisa cada módulo instalado." (retry)="os.reload()">
      @if (os.running(); as r) {
        <section class="card"><h2>{{ r.mode === 'analyze' ? 'Análisis en curso' : 'Migración en curso' }}</h2>
          @if (r.jobId) { <mf-job-log [jobId]="r.jobId" (finished)="os.reload()" /> }</section>
      }
      @if (os.analysis(); as a) {
        <section class="card">
          <h2>Base analizada: Odoo {{ a.summary['sourceVersion'] }}</h2>
          <p class="small">Saltos previstos: {{ a.summary['from'] }} → {{ (a.summary['hops'] ?? []).join(' → ') }}.</p>
          <div class="cards">
            <article><b>{{ a.summary['counts']?.modules }}</b>módulos instalados</article>
            <article><b>{{ a.summary['counts']?.core }}</b>del núcleo de Odoo</article>
            <article><b>{{ a.summary['counts']?.oca }}</b>OCA</article>
            <article><b>{{ a.summary['counts']?.thirdParty }}</b>de terceros / a la medida</article>
            <article><b [class.bad]="os.pending().length">{{ os.pending().length }}</b>bloquean sin decisión</article>
            <article><b>{{ a.summary['counts']?.warn }}</b>con advertencia</article>
          </div>
          @if (os.pending().length) {
            <p-message severity="warn"><span>{{ os.pending().length }} módulo(s) no tienen versión 19 o no están portados en todas las versiones.
              <a [routerLink]="['/p', os.ctx.projectId(), 'odoo', 'modulos']">Revíselos en Módulos</a>: un arquitecto decide (con motivo) si se desinstalan antes de migrar.</span></p-message>
          }
        </section>
      }
      @if (os.migration(); as m) {
        <section class="card">
          <h2>Migración <mf-status [value]="m.state" /></h2>
          <div class="hops">
            @for (h of m.hops; track h.version) {
              <div class="hop" [class]="'hop ' + h.state"><b>{{ h.version }}</b>{{ hopState(h.state) }}@if (h.errors) { · {{ h.errors }} error(es) }</div>
            }
          </div>
          @if (failed(); as f) { <p>Log del salto a {{ f.version }} (últimas líneas):</p><pre>{{ f.logTail }}</pre> }
          @if (m.reconciliation.length) {
            <h2>Conciliación antes / después</h2>
            <p-table [value]="m.reconciliation">
              <ng-template #header><tr><th scope="col">Dato</th><th scope="col">Odoo {{ os.analysis()?.summary?.['sourceVersion'] ?? '12' }}</th><th scope="col">Odoo 19</th><th scope="col">Resultado</th></tr></ng-template>
              <ng-template #body let-r><tr><td>{{ r.label }}</td><td>{{ r.before?.join(' · ') ?? '—' }}</td><td>{{ r.after?.join(' · ') ?? '—' }}</td>
                <td [class.ok]="r.state === 'EQUAL'" [class.bad]="r.state === 'DIFFERENT'">{{ r.state === 'EQUAL' ? 'Igual' : r.state === 'DIFFERENT' ? 'Distinto' : 'No aplica' }}</td></tr></ng-template>
            </p-table>
          }
          @if (m.summary['artifacts']?.['dump']; as d) {
            <p style="margin-top:12px"><mf-action label="Descargar la base en Odoo 19 (pg_dump)" icon="pi pi-download" (run)="api.download(d, 'odoo19.dump')" /></p>
          }
          @if (m.summary['uninstalled']?.length) { <p class="small">Desinstalados antes de migrar (decisión registrada): {{ m.summary['uninstalled'].join(', ') }}.</p> }
        </section>
      }
    </mf-state>`,
})
export class OdooOverview {
  os = inject(OdooStore);
  api = inject(Api);
  failed = computed(() => this.os.migration()?.hops.find(h => h.state !== 'succeeded') ?? null);
  hopState(s: string) { return HOP_STATE[s] ?? s; }
  analyzeReason() {
    if (!this.os.ctx.can('execute')) return `Su rol (${this.os.ctx.role()}) no permite ejecutar`;
    return this.os.running() ? 'Hay un análisis o una migración en curso' : null;
  }
  migrateReason() {
    if (!this.os.ctx.can('execute')) return `Su rol (${this.os.ctx.role()}) no permite ejecutar`;
    if (this.os.running()) return 'Hay un análisis o una migración en curso';
    if (!this.os.analysis()) return 'Primero analice la base';
    const n = this.os.pending().length;
    return n ? `${n} módulo(s) bloquean sin decisión: revise Módulos` : null;
  }
}

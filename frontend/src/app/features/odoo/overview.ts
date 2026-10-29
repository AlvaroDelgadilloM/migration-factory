import { Component, computed, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { InputTextModule } from 'primeng/inputtext';
import { MessageModule } from 'primeng/message';
import { TableModule } from 'primeng/table';
import { Api } from '../../core/api';
import { ActionButton, JobLog, PageHeader, StateView, StatusTag } from '../../shared/ui';
import { OdooStore } from './store';

const HOP_STATE: Record<string, string> = { succeeded: 'Listo', failed: 'Falló', cancelled: 'Cancelado', timed_out: 'Sin ejecutor', running: 'En curso' };

/** Odoo 12 -> 19 · Resumen: analyze the uploaded database, then migrate it hop by hop and reconcile before/after. */
@Component({
  selector: 'mf-odoo-overview',
  imports: [FormsModule, InputTextModule, RouterLink, MessageModule, TableModule, ActionButton, JobLog, PageHeader, StateView, StatusTag],
  styles: [`
    .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(9rem, 1fr)); gap: 10px; margin: 10px 0 16px; }
    .cards article { background: #fff; border: 1px solid var(--mf-line); border-radius: 12px; padding: 10px 14px; }
    .cards b { display: block; font-size: 22px; } .card { background: #fff; border: 1px solid var(--mf-line); border-radius: 12px; padding: 14px 18px; margin: 0 0 16px; }
    .hops { display: grid; grid-template-columns: repeat(auto-fit, minmax(8rem, 1fr)); gap: 8px; margin: 8px 0; }
    .hop { border: 1px solid var(--mf-line); border-radius: 10px; padding: 8px 10px; font-size: 13px; }
    .hop b { display: block; font-size: 15px; } .hop.succeeded { border-color: #12b76a; } .hop.failed, .hop.timed_out { border-color: #d92d20; }
    pre { background: #0f1720; color: #e2e8f0; padding: 10px; border-radius: 8px; font-size: 12px; max-height: 22rem; overflow: auto; white-space: pre-wrap; }
    .fix { border-top: 1px solid var(--mf-line); padding: 10px 0 0; margin-top: 10px; } .fix h3 { font-size: 15px; margin: 0 0 4px; }
    .decide { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 6px; } .decide input { flex: 1 1 14rem; min-width: 0; }
    button.link { background: none; border: 0; color: var(--mf-teal); cursor: pointer; padding: 0; }
    .bad { color: #b42318; } .ok { color: #067647; } h2 { font-size: 17px; margin: 0 0 8px; }`],
  template: `
    <mf-page-header title="Resumen" crumb="ODOO 12 → 19" subtitle="Base de datos de Odoo Community migrada con OpenUpgrade, salto por salto, sobre una copia.">
      <mf-action label="Analizar la base" icon="pi pi-search" [outlined]="!!os.analysis()" [busy]="os.busy()" [disabledReason]="analyzeReason()" (run)="os.start('analyze')" />
      <mf-action label="Migrar a Odoo 19" icon="pi pi-play" [busy]="os.busy()" [disabledReason]="migrateReason()" (run)="os.start('migrate')" />
    </mf-page-header>
    @if (os.error(); as e) { <p-message severity="error">{{ e.code }}: {{ e.message }}</p-message> }
    @if (os.codeOnly()) {
      <section class="card">
        <h2>Proyecto de solo código</h2>
        <p class="small">El ZIP trae el código de {{ os.uploaded()['odooModules']?.length }} módulo(s) a la medida y no el respaldo de la base: no hay datos
          que analizar ni migrar. El resultado es el código portado a Odoo 19, listo para instalar en un Odoo 19
          (<a [routerLink]="['/p', os.ctx.projectId(), 'odoo', 'codigo']">pantalla Código</a>). Para migrar también los datos, registre un proyecto con un
          ZIP que traiga el respaldo de Odoo 12 (dump.sql) junto a los módulos.</p>
        @if (os.code(); as c) {
          <div class="cards">
            <article><b>{{ c.summary['counts']?.modules }}</b>módulos</article>
            <article><b [class.ok]="c.summary['counts']?.installed === c.summary['counts']?.modules">{{ c.summary['counts']?.installed }}</b>instalan en Odoo 19</article>
            <article><b>{{ c.summary['counts']?.AUTO }}</b>cambios automáticos</article>
            <article><b [class.bad]="c.summary['counts']?.MANUAL">{{ c.summary['counts']?.MANUAL }}</b>manuales pendientes</article>
            <article><b>{{ c.summary['counts']?.REVIEW }}</b>a revisar</article>
          </div>
          <p>
            @if (c.summary['artifacts']?.['ported']; as a) { <mf-action label="Código portado (.zip)" icon="pi pi-download" (run)="api.download(a, 'modulos-odoo19.zip')" /> }
            <a [routerLink]="['/p', os.ctx.projectId(), 'odoo', 'codigo']" style="margin-left:12px">Ver el detalle por archivo y línea →</a>
          </p>
        } @else if (!os.running()) {
          <p><a [routerLink]="['/p', os.ctx.projectId(), 'odoo', 'codigo']">Ir a Código y pulsar «Portar código a 19» →</a></p>
        }
      </section>
    }
    <mf-state [loading]="os.list.loading()" [error]="os.list.error()" [hasData]="!!os.list.data()" [empty]="os.list.data()?.length === 0 && !os.codeOnly()"
              emptyText="Aún no se analiza la base. «Analizar la base» la restaura en un servidor aislado y revisa cada módulo instalado." (retry)="os.reload()">
      @if (os.running(); as r) {
        <section class="card"><h2>{{ r.mode === 'analyze' ? 'Análisis en curso' : r.mode === 'code' ? 'Port de código en curso' : 'Migración en curso' }}</h2>
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
            <article><b>{{ a.summary['counts']?.thirdParty }}</b>de terceros sin código</article>
            <article><b>{{ a.summary['counts']?.custom ?? 0 }}</b>a la medida con código</article>
            <article><b [class.bad]="os.pending().length">{{ os.pending().length }}</b>bloquean sin decisión</article>
            <article><b>{{ a.summary['counts']?.warn }}</b>con advertencia</article>
          </div>
          @if (os.pending().length) {
            <p-message severity="warn"><span>{{ os.pending().length }} módulo(s) no tienen versión 19 o no están portados en todas las versiones.
              <a [routerLink]="['/p', os.ctx.projectId(), 'odoo', 'modulos']">Revíselos en Módulos</a>: un arquitecto decide (con motivo) si se desinstalan antes de migrar.</span></p-message>
          }
        </section>
        @if (a.summary['fixes']?.length) {
          <section class="card">
            <h2>Correcciones de datos sugeridas</h2>
            <p class="small">Datos de la base que hacen fallar un salto de OpenUpgrade. Si se aprueban, se corrigen solo en la copia que se migra,
              antes del primer salto; la base original no cambia. Cada una necesita la decisión de un arquitecto, con motivo.</p>
            @for (f of a.summary['fixes']; track f.id) {
              <div class="fix">
                <h3>{{ f.title }} ({{ f.count }})</h3>
                <p class="small">{{ f.why }}<br /><strong>Corrección:</strong> {{ f.fix }}</p>
                <p class="small">Registros: @for (r of f.records; track r.id + r.detail) { <code>#{{ r.id }} {{ r.name }}</code> ({{ r.detail }})@if (!$last) { · } }</p>
                @if (a.decisions['fix:' + f.id]; as d) {
                  <p class="small"><strong>{{ d.action === 'apply' ? 'Se corrige en la copia' : 'Se migra sin corregir' }}</strong> · {{ d.reason }} · {{ d.by }}
                    @if (os.ctx.can('plan')) { <button class="link" (click)="os.decide('fix:' + f.id, 'pending', '')">deshacer</button> }</p>
                } @else if (os.ctx.can('plan')) {
                  <div class="decide"><input pInputText [(ngModel)]="reasons[f.id]" [attr.aria-label]="'Motivo: ' + f.title" placeholder="Motivo (queda registrado)" />
                    <button class="p-button p-button-sm" [disabled]="(reasons[f.id] ?? '').trim().length < 10" (click)="os.decide('fix:' + f.id, 'apply', reasons[f.id])">Corregir en la copia</button>
                    <button class="p-button p-button-sm p-button-outlined" [disabled]="(reasons[f.id] ?? '').trim().length < 10" (click)="os.decide('fix:' + f.id, 'ignore', reasons[f.id])">Migrar sin corregir</button></div>
                } @else { <p class="small muted">Pendiente: decide un arquitecto.</p> }
              </div>
            }
          </section>
        }
      }
      @if (os.migration(); as m) {
        <section class="card">
          <h2>Migración <mf-status [value]="m.state" /></h2>
          <div class="hops">
            @for (h of m.hops; track h.version) {
              <div class="hop" [class]="'hop ' + h.state"><b>{{ h.version }}</b>{{ hopState(h.state) }}@if (h.errors) { · {{ h.errors }} error(es) }</div>
            }
          </div>
          @for (h of m.hops; track h.version) {
            @if (h.state === 'succeeded' && h.errors) {
              <details><summary class="small">Salto a {{ h.version }}: terminó, con {{ h.errors }} línea(s) ERROR no fatales en el log (revisar)</summary><pre>{{ h.logTail }}</pre></details>
            }
          }
          @if (failed(); as f) { <p>Log del salto a {{ f.version }} (últimas líneas):</p><pre>{{ f.logTail }}</pre> }
          @if (m.reconciliation.length) {
            <h2>Conciliación antes / después</h2>
            <p-table [value]="m.reconciliation">
              <ng-template #header><tr><th scope="col">Dato</th><th scope="col">Odoo {{ os.analysis()?.summary?.['sourceVersion'] ?? '12' }}</th><th scope="col">Odoo 19</th><th scope="col">Resultado</th></tr></ng-template>
              <ng-template #body let-r><tr><td>{{ r.label }}</td><td>{{ r.before?.join(' · ') ?? '—' }}</td><td>{{ r.after?.join(' · ') ?? '—' }}</td>
                <td [class.ok]="r.state === 'EQUAL'" [class.bad]="r.state === 'DIFFERENT' && !r.info">{{ r.state === 'EQUAL' ? 'Igual' : r.state === 'DIFFERENT' ? 'Distinto' : 'No aplica' }}</td></tr></ng-template>
            </p-table>
          }
          @if (m.summary['artifacts']?.['dump']; as d) {
            <p style="margin-top:12px"><mf-action label="Descargar respaldo Odoo 19 (.zip)" icon="pi pi-download" (run)="api.download(d, 'odoo19.zip')" />
              <span class="small"> dump.sql + manifest.json{{ m.summary['filestore'] ? ' + filestore' : '' }}: se restaura desde el gestor de bases de datos de Odoo 19.</span></p>
          }
          @if (m.summary['fixesApplied']?.length) { <p class="small">Correcciones de datos aplicadas a la copia (aprobadas): {{ m.summary['fixesApplied'].join(', ') }}.</p> }
          @if (m.summary['technicalFixes']?.length) { <p class="small">Correcciones técnicas entre saltos (fallos conocidos de OpenUpgrade, sin cambio de significado): {{ m.summary['technicalFixes'].join(' · ') }}.</p> }
          @if (m.summary['customModules']?.length) { <p class="small">Módulos a la medida migrados con su código portado: {{ m.summary['customModules'].join(', ') }}.</p> }
          @if (m.summary['uninstalled']?.length) { <p class="small">Desinstalados antes de migrar (decisión registrada): {{ m.summary['uninstalled'].join(', ') }}.</p> }
        </section>
      }
    </mf-state>`,
})
export class OdooOverview {
  os = inject(OdooStore);
  api = inject(Api);
  reasons: Record<string, string> = {};
  failed = computed(() => this.os.migration()?.hops.find(h => h.state !== 'succeeded') ?? null);
  hopState(s: string) { return HOP_STATE[s] ?? s; }
  analyzeReason() {
    if (!this.os.ctx.can('execute')) return `Su rol (${this.os.ctx.role()}) no permite ejecutar`;
    if (this.os.codeOnly()) return 'El ZIP trae solo código de módulos, sin respaldo de la base: el resultado está en Código';
    return this.os.running() ? 'Hay un análisis o una migración en curso' : null;
  }
  migrateReason() {
    if (!this.os.ctx.can('execute')) return `Su rol (${this.os.ctx.role()}) no permite ejecutar`;
    if (this.os.running()) return 'Hay un análisis o una migración en curso';
    if (this.os.codeOnly()) return 'Sin respaldo de la base no hay datos que migrar: el código portado está en Código';
    if (!this.os.analysis()) return 'Primero analice la base';
    const n = this.os.pending().length;
    if (n) return `${n} módulo(s) bloquean sin decisión: revise Módulos`;
    const f = this.os.pendingFixes().length;
    return f ? `${f} corrección(es) de datos sin decisión` : null;
  }
}

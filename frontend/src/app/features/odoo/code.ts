import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MessageModule } from 'primeng/message';
import { SelectButtonModule } from 'primeng/selectbutton';
import { Api } from '../../core/api';
import { ActionButton, JobLog, PageHeader, StateView, StatusTag } from '../../shared/ui';
import { OdooStore } from './store';

const STATUS: Record<string, string> = { AUTO: 'Automático', MANUAL: 'Manual', REVIEW: 'Revisar' };
const INSTALL: Record<string, string> = { succeeded: 'Instala en 19', failed: 'No instala en 19', skipped: 'Sin probar', cancelled: 'Cancelado', timed_out: 'Sin ejecutor' };

interface Finding { pattern: string; file: string; line: number; text: string; since: string; target: string; note: string; status: string }
interface CodeModule { name: string; version: string; depends: string[]; findings: Finding[]; counts: Record<string, number>;
  install?: { state: string; errors: number; logTail: string; tests?: { total: number; failed: number; errors: number; failures?: string[]; smoke?: string[] } } }

/** Odoo 12 -> 19 · Código: custom module code uploaded with the project. What changes (file and line), what the automatic port
 *  (OCA odoo-module-migrator) did, what is left for a person, and whether each ported module installs on a new Odoo 19. */
@Component({
  selector: 'mf-odoo-code',
  imports: [FormsModule, MessageModule, SelectButtonModule, ActionButton, JobLog, PageHeader, StateView, StatusTag],
  styles: [`
    .card { background: #fff; border: 1px solid var(--mf-line); border-radius: 12px; padding: 14px 18px; margin: 0 0 16px; }
    .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(9rem, 1fr)); gap: 10px; margin: 10px 0 16px; }
    .cards article { background: #fff; border: 1px solid var(--mf-line); border-radius: 12px; padding: 10px 14px; } .cards b { display: block; font-size: 22px; }
    h2 { font-size: 17px; margin: 0 0 6px; display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
    .tag { font-size: 12px; padding: 2px 8px; border-radius: 10px; white-space: nowrap; }
    .t-AUTO, .t-succeeded { background: #e7f6ec; color: #067647; } .t-MANUAL, .t-failed { background: #fdecea; color: #b42318; }
    .t-REVIEW, .t-skipped { background: #fff4e5; color: #b54708; }
    .c-KEEP { background: #e7f6ec; color: #067647; } .c-MIGRATE, .c-MOVE_TO_ORM { background: #eef4ff; color: #3538cd; }
    .c-REWRITE, .c-MANUAL_REVIEW { background: #fff4e5; color: #b54708; } .c-DEPRECATE { background: #f2f4f7; color: #475467; }
    .tag + .tag { margin-left: 6px; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; } td, th { text-align: left; padding: 6px 8px; border-top: 1px solid var(--mf-line); vertical-align: top; }
    .scroll { overflow-x: auto; } code { font-size: 12px; } .small { font-size: 12.5px; } .muted { color: var(--mf-muted); }
    pre { background: #0f1720; color: #e2e8f0; padding: 10px; border-radius: 8px; font-size: 12px; max-height: 18rem; overflow: auto; white-space: pre-wrap; }
    .toolbar { display: flex; gap: 10px; flex-wrap: wrap; margin: 0 0 12px; }`],
  template: `
    <mf-page-header title="Código" crumb="ODOO 12 → 19" subtitle="Módulos a la medida: qué cambia, qué se porta solo y si instalan en Odoo 19.">
      <mf-action label="Portar código a 19" icon="pi pi-cog" [outlined]="!!os.code()" [busy]="os.busy()" [disabledReason]="reason()" (run)="os.start('code')" />
    </mf-page-header>
    @if (os.error(); as e) { <p-message severity="error">{{ e.code }}: {{ e.message }}</p-message> }
    <mf-state [loading]="os.list.loading()" [error]="os.list.error()" [hasData]="!!os.code() || !!running()" [empty]="!os.code() && !running()"
              emptyText="Suba el ZIP con los módulos (carpetas con __manifest__.py) y pulse «Portar código a 19»: se inventarían, se portan con la herramienta de OCA y cada uno se instala y se prueba (sus pruebas y una prueba funcional generada) en un Odoo 19 nuevo y aislado con datos demo." (retry)="os.reload()">
      @if (running(); as r) {
        <section class="card"><h2>Portando código</h2>@if (r.jobId) { <mf-job-log [jobId]="r.jobId" (finished)="os.reload()" /> }</section>
      }
      @if (os.code(); as c) {
        <section class="card">
          <h2>Resultado <mf-status [value]="c.state" /></h2>
          <div class="cards">
            <article><b>{{ c.summary['counts']?.modules }}</b>módulos</article>
            <article><b>{{ c.summary['counts']?.installed }}</b>instalan en Odoo 19</article>
            @if (c.summary['counts']?.tested) { <article><b>{{ c.summary['counts']?.testsPassed }}/{{ c.summary['counts']?.tested }}</b>pasan sus pruebas</article> }
            <article><b>{{ c.summary['counts']?.AUTO }}</b>cambios automáticos</article>
            <article><b>{{ c.summary['counts']?.MANUAL }}</b>manuales pendientes</article>
            <article><b>{{ c.summary['counts']?.REVIEW }}</b>a revisar</article>
          </div>
          <p class="small muted">Automático: la herramienta de OCA lo cambió y ya no aparece en el código portado. Manual: sigue en el código
            portado. Revisar: necesita una persona aunque compile (SQL directo, tablas fuera del ORM, semántica). «Instala en 19»: el módulo
            portado se instaló en un Odoo 19 nuevo con sus dependencias; no reemplaza las pruebas funcionales.</p>
          @if (c.summary['base']?.state && c.summary['base'].state !== 'succeeded') {
            <p-message severity="error">No se pudo crear el Odoo 19 base ({{ c.summary['base'].modules?.join(', ') }}); no se probó ningún módulo.</p-message>
            <pre>{{ c.summary['base'].logTail }}</pre>
          }
          <p class="toolbar">
            @if (c.summary['artifacts']?.['ported']; as a) { <mf-action label="Código portado (.zip)" icon="pi pi-download" [outlined]="true" (run)="api.download(a, 'modulos-odoo19.zip')" /> }
            @if (c.summary['artifacts']?.['diff']; as a) { <mf-action label="Cambios (.diff)" icon="pi pi-download" [outlined]="true" (run)="api.download(a, 'port-odoo19.diff')" /> }
            @if (c.summary['artifacts']?.['report']; as a) { <mf-action label="Inventario (.json)" icon="pi pi-download" [outlined]="true" (run)="api.download(a, 'inventario-odoo19.json')" /> }
          </p>
          <p-selectbutton [options]="filters" optionLabel="label" optionValue="value" [ngModel]="filter()" (ngModelChange)="filter.set($event)" aria-label="Filtrar cambios" />
        </section>
        @for (m of modules(); track m.name) {
          <section class="card">
            <h2><code>{{ m.name }}</code> <span class="small muted">{{ m.version }} · depende de {{ m.depends.join(', ') || '—' }}</span>
              @if (m.install; as i) { <span [class]="'tag t-' + i.state">{{ install(i.state) }}</span>
                @if (i.tests; as t) { <span [class]="'tag t-' + (t.failed || t.errors ? 'failed' : 'succeeded')">pruebas {{ t.total - t.failed - t.errors }}/{{ t.total }} bien</span> } }</h2>
            @if (m.install?.tests; as t) {
              @if (t.failures?.length) { <pre>{{ t.failures?.join('\n') }}</pre> }
              @if (t.smoke?.length) { <p class="small muted">Prueba funcional generada (leer todos los campos y abrir vistas, con datos demo): {{ t.smoke?.join(' · ') }}</p> }
            }
            <div class="scroll"><table>
              <tr><th scope="col">Estado</th><th scope="col">Dónde</th><th scope="col">Qué</th><th scope="col">En 19</th></tr>
              @for (f of shown(m); track f.file + f.line + f.pattern) {
                <tr><td><span [class]="'tag t-' + f.status">{{ status(f.status) }}</span></td>
                  <td><code>{{ f.file }}:{{ f.line }}</code></td>
                  <td><code>{{ f.text }}</code>@if (f.note) { <div class="small muted">{{ f.note }}</div> }</td>
                  <td class="small">{{ f.target }}@if (f.since !== '—') { <span class="muted"> (desde {{ f.since }})</span> }</td></tr>
              } @empty { <tr><td colspan="4" class="muted">Nada que mostrar con este filtro.</td></tr> }
            </table></div>
            @if (m.install?.state === 'failed') { <p class="small">Log de la instalación en Odoo 19 (últimas líneas):</p><pre>{{ m.install?.logTail }}</pre> }
          </section>
        }
        @if (c.summary['externalSql']?.length) {
          <section class="card">
            <h2>SQL externo ({{ c.summary['externalSql'].length }} objetos)</h2>
            <p class="small muted">Objetos fuera del ORM que vienen en los .sql del ZIP: clasificados contra el código de los módulos (regla y motivo, no
              puntaje) y ejecutados sentencia por sentencia en el Odoo 19 nuevo antes de instalar los módulos.</p>
            <p class="small">@for (k of sqlCats; track k) { @if (c.summary['externalSqlCounts']?.[k]) { <span [class]="'tag c-' + k">{{ k }} {{ c.summary['externalSqlCounts'][k] }}</span> } }</p>
            <div class="scroll"><table>
              <tr><th scope="col">Objeto</th><th scope="col">Clasificación</th><th scope="col">Motivo</th><th scope="col">Lo usan</th><th scope="col">En Odoo 19</th></tr>
              @for (o of c.summary['externalSql']; track o.file + o.line) {
                <tr><td><code>{{ o.schema }}{{ o.name ? '.' + o.name : '' }}</code><br /><span class="small muted">{{ o.kind }}@if (o.rows) { · {{ o.rows }} fila(s) de datos } · {{ o.file }}:{{ o.line }}</span></td>
                  <td><span [class]="'tag c-' + o.category">{{ o.category }}</span></td>
                  <td class="small">{{ o.reason }}</td>
                  <td class="small">{{ usedBy(o) }}</td>
                  <td class="small">@if (o.in19?.state === 'ok') { <span class="tag t-succeeded">se crea</span> } @else if (o.in19?.state === 'error') { <span class="tag t-failed">error</span> <span class="muted">{{ o.in19.error }}</span> } @else { — }</td></tr>
              }
            </table></div>
          </section>
        }
        @if (c.summary['portWarnings']?.length) {
          <section class="card"><h2>Avisos de la herramienta de port</h2><pre>{{ c.summary['portWarnings'].join('\\n') }}</pre></section>
        }
      }
    </mf-state>`,
})
export class OdooCode {
  os = inject(OdooStore);
  api = inject(Api);
  filter = signal('PENDING');
  filters = [{ label: 'Manuales y a revisar', value: 'PENDING' }, { label: 'Todos', value: 'ALL' }];
  running = computed(() => { const r = this.os.running(); return r?.mode === 'code' ? r : null; });
  modules = computed(() => (this.os.code()?.modules ?? []) as unknown as CodeModule[]);
  shown(m: CodeModule) { return this.filter() === 'ALL' ? m.findings : m.findings.filter(f => f.status !== 'AUTO'); }
  sqlCats = ['KEEP', 'MIGRATE', 'REWRITE', 'MOVE_TO_ORM', 'DEPRECATE', 'MANUAL_REVIEW'];
  usedBy(o: { uses?: { module: string; how: string }[] }) {
    const how: Record<string, string> = { model: 'modelo', write: 'escribe', read: 'lee', call: 'llama', refresh: 'refresca' };
    const by = new Map<string, Set<string>>();
    for (const u of o.uses ?? []) by.set(u.module, (by.get(u.module) ?? new Set()).add(how[u.how] ?? u.how));
    return [...by].map(([m, h]) => `${m} (${[...h].join(', ')})`).join(' · ') || '—';
  }
  status(s: string) { return STATUS[s] ?? s; }
  install(s: string) { return INSTALL[s] ?? s; }
  reason() {
    if (!this.os.ctx.can('execute')) return `Su rol (${this.os.ctx.role()}) no permite ejecutar`;
    return this.os.running() ? 'Hay un análisis, una migración o un port en curso' : null;
  }
}

import { Component, computed, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { MessageModule } from 'primeng/message';
import { Api } from '../../core/api';
import { UiError } from '../../core/errors';
import { ActionButton, JobLog, PageHeader, StateView, StatusTag } from '../../shared/ui';
import { OdooStore } from './store';

interface Check { check: string; ok: boolean; got?: string; error?: string }

/** Odoo 12 -> 19 · Pruebas: the project's business flows (Python checks) run against the latest code port, on a new Odoo 19 with
 *  demo data and the project's external SQL, in the isolated runner. Each flow is OK or FALLA with what it returned or the error. */
@Component({
  selector: 'mf-odoo-flows',
  imports: [FormsModule, RouterLink, MessageModule, ActionButton, JobLog, PageHeader, StateView, StatusTag],
  styles: [`
    .card { background: #fff; border: 1px solid var(--mf-line); border-radius: 12px; padding: 14px 18px; margin: 0 0 16px; }
    h2 { font-size: 17px; margin: 0 0 8px; display: flex; gap: 10px; align-items: center; }
    textarea { width: 100%; min-height: 26rem; font: 12.5px/1.5 ui-monospace, Menlo, monospace; border: 1px solid var(--mf-line);
      border-radius: 8px; padding: 10px; background: #fbfcfd; resize: vertical; }
    .bar { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin: 8px 0 0; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; } td, th { text-align: left; padding: 6px 8px; border-top: 1px solid var(--mf-line); vertical-align: top; }
    .tag { font-size: 12px; padding: 2px 8px; border-radius: 10px; } .ok { background: #e7f6ec; color: #067647; } .bad { background: #fdecea; color: #b42318; }
    pre { background: #0f1720; color: #e2e8f0; padding: 10px; border-radius: 8px; font-size: 12px; max-height: 18rem; overflow: auto; white-space: pre-wrap; }
    .small { font-size: 12.5px; } .muted { color: var(--mf-muted); } .err { color: #b42318; }`],
  template: `
    <mf-page-header title="Pruebas" crumb="ODOO 12 → 19" subtitle="Flujos de negocio contra el código portado, en un Odoo 19 nuevo con datos demo.">
      <mf-action label="Ejecutar flujos" icon="pi pi-play" [busy]="os.busy()" [disabledReason]="runReason()" (run)="os.start('flows')" />
    </mf-page-header>
    @if (os.error(); as e) { <p-message severity="error">{{ e.code }}: {{ e.message }}</p-message> }
    @if (running(); as r) {
      <section class="card"><h2>Ejecutando flujos</h2>@if (r.jobId) { <mf-job-log [jobId]="r.jobId" (finished)="os.reload()" /> }</section>
    }
    @if (os.flows(); as f) {
      <section class="card">
        <h2>Último resultado <mf-status [value]="f.state" />
          @if (f.summary['counts']; as c) { <span [class]="'tag ' + (c.ok === c.total && c.total ? 'ok' : 'bad')">{{ c.ok }}/{{ c.total }} flujos bien</span> }</h2>
        <p class="small muted">Sobre el port de código del {{ f.createdAt.slice(0, 16).replace('T', ' ') }} · los módulos instalados con sus dependencias, el SQL externo
          del proyecto y datos demo; la base se borra al terminar.</p>
        @if (checks(f).length) {
          <table>
            <tr><th scope="col">Resultado</th><th scope="col">Flujo</th><th scope="col">Detalle</th></tr>
            @for (c of checks(f); track c.check) {
              <tr><td><span [class]="'tag ' + (c.ok ? 'ok' : 'bad')">{{ c.ok ? 'OK' : 'FALLA' }}</span></td><td>{{ c.check }}</td>
                <td class="small" [class.err]="!c.ok"><code>{{ c.ok ? c.got : c.error }}</code></td></tr>
            }
          </table>
        }
        @if (f.summary['logTail']) {
          <p class="small">{{ f.summary['stage'] === 'flows' ? 'El script no terminó (error de Python):' : 'No se pudieron instalar los módulos:' }}</p>
          <pre>{{ f.summary['logTail'] }}</pre>
        }
      </section>
    }
    <mf-state [loading]="loading()" [error]="loadError()" [hasData]="loaded()" [empty]="false" (retry)="load()">
      <section class="card">
        <h2>Flujos de este proyecto @if (isTemplate()) { <span class="tag ok">plantilla</span> }</h2>
        <p class="small muted">Python para <code>odoo shell</code>: cada <code>check(nombre, función)</code> corre en su propio savepoint y no detiene a los demás.
          Ayudas: <code>env</code>, <code>MODULES</code>, <code>product()</code>, <code>order(cliente, cantidad, precio, **campos)</code>, <code>lot()</code>.
          Corre en el ejecutor aislado de Odoo 19 (sin internet ni acceso a la plataforma), nunca en el servidor.</p>
        <textarea [ngModel]="content()" (ngModelChange)="content.set($event)" spellcheck="false" aria-label="Flujos de negocio en Python"></textarea>
        <div class="bar">
          <mf-action label="Guardar" icon="pi pi-save" [busy]="saving()" [disabledReason]="saveReason()" (run)="save()" />
          @if (dirty()) { <span class="small muted">Cambios sin guardar: se ejecuta lo guardado.</span> }
          @if (saveError(); as e) { <span class="small err">{{ e.message }}</span> }
          @if (updatedBy(); as u) { <span class="small muted">Guardado por {{ u }}</span> }
        </div>
        @if (!os.code()) {
          <p-message severity="info"><span>Primero porte el código en <a [routerLink]="['/p', os.ctx.projectId(), 'odoo', 'codigo']">Código</a>: los flujos corren sobre ese código.</span></p-message>
        }
      </section>
    </mf-state>`,
})
export class OdooFlows {
  os = inject(OdooStore);
  private api = inject(Api);
  content = signal('');
  saved = signal('');
  isTemplate = signal(true);
  updatedBy = signal<string | null>(null);
  loading = signal(false);
  loaded = signal(false);
  loadError = signal<UiError | null>(null);
  saving = signal(false);
  saveError = signal<UiError | null>(null);
  dirty = computed(() => this.content() !== this.saved());
  running = computed(() => { const r = this.os.running(); return r?.mode === 'flows' ? r : null; });
  private loadedFor: string | null = null;

  constructor() {
    effect(() => {
      const id = this.os.ctx.projectId();
      if (id && id !== this.loadedFor) { this.loadedFor = id; this.load(); }
    });
  }

  async load() {
    const id = this.os.ctx.projectId();
    if (!id) return;
    this.loading.set(true);
    this.loadError.set(null);
    try {
      const f = await this.api.odooFlows(id);
      this.content.set(f.content); this.saved.set(f.isTemplate ? '' : f.content); this.isTemplate.set(f.isTemplate); this.updatedBy.set(f.updatedBy);
      this.loaded.set(true);
    } catch (e) { this.loadError.set(UiError.from(e)); } finally { this.loading.set(false); }
  }

  async save() {
    const id = this.os.ctx.projectId();
    if (!id) return;
    this.saving.set(true);
    this.saveError.set(null);
    try {
      const f = await this.api.saveOdooFlows(id, this.content());
      this.saved.set(f.content); this.isTemplate.set(false); this.updatedBy.set(f.updatedBy);
    } catch (e) { this.saveError.set(UiError.from(e)); } finally { this.saving.set(false); }
  }

  checks(f: { summary: Record<string, any> }): Check[] { return (f.summary['checks'] ?? []) as Check[]; }
  saveReason() {
    if (!this.os.ctx.can('execute')) return `Su rol (${this.os.ctx.role()}) no permite editar`;
    return this.dirty() || this.isTemplate() ? null : 'Sin cambios';
  }
  runReason() {
    if (!this.os.ctx.can('execute')) return `Su rol (${this.os.ctx.role()}) no permite ejecutar`;
    if (this.os.running()) return 'Hay un análisis, una migración o un port en curso';
    if (!this.os.code()) return 'Primero porte el código a 19 (pantalla Código)';
    if (this.isTemplate()) return 'Guarde los flujos primero (puede guardar la plantilla tal cual)';
    return null;
  }
}

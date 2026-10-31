import { Component, computed, inject, signal } from '@angular/core';
import { NgTemplateOutlet } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { MessageModule } from 'primeng/message';
import { SelectModule } from 'primeng/select';
import { TagModule } from 'primeng/tag';
import { Api } from '../../core/api';
import { ActionButton, JobLog, PageHeader, StateView } from '../../shared/ui';
import { ApexStore } from './store';

export const SEVERITY: Record<string, { label: string; s: 'danger' | 'warn' | 'secondary' }> = {
  BLOCK: { label: 'Bloquea', s: 'danger' }, REVIEW: { label: 'Revisar', s: 'warn' }, INFO: { label: 'Informativo', s: 'secondary' },
};
export const RULE_STATUS: Record<string, string> = {
  DESUPPORTED: 'Retirado', DEPRECATED: 'Deprecado', LEGACY: 'Legacy', UNCONFIRMED: 'Sin aviso oficial', INTERNAL: 'No es API pública',
};

/** Oracle APEX · Resumen: what the applications bring and the plan of jumps to the chosen version. Phase 1: analysis, without a database. */
@Component({
  selector: 'mf-apex-overview',
  imports: [FormsModule, NgTemplateOutlet, RouterLink, MessageModule, SelectModule, TagModule, ActionButton, JobLog, PageHeader, StateView],
  styles: [`
    .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(9rem, 1fr)); gap: 10px; margin: 10px 0 16px; }
    .cards article { background: #fff; border: 1px solid var(--mf-line); border-radius: 12px; padding: 10px 14px; }
    .cards b { display: block; font-size: 22px; } .bad { color: #b42318; }
    .card { background: #fff; border: 1px solid var(--mf-line); border-radius: 12px; padding: 14px 18px; margin: 0 0 16px; }
    h2 { font-size: 17px; margin: 0 0 8px; } h3 { font-size: 15px; margin: 0; }
    .controls { display: flex; flex-wrap: wrap; gap: 12px; align-items: end; margin-bottom: 14px; } .controls label { display: block; font-size: 12px; color: var(--mf-muted); margin-bottom: 4px; }
    .route { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin: 0 0 12px; } .route span { font-weight: 600; } .route i { color: var(--mf-muted); font-size: 11px; }
    .hop { border: 1px solid var(--mf-line); border-radius: 10px; margin: 0 0 8px; }
    .hop > button { all: unset; box-sizing: border-box; width: 100%; display: flex; gap: 10px; align-items: center; padding: 10px 12px; cursor: pointer; flex-wrap: wrap; }
    .hop > button:focus-visible { outline: 2px solid var(--mf-teal); outline-offset: -2px; } .grow { flex: 1 1 8rem; }
    .changes { padding: 0 12px 10px; } .change { border-top: 1px solid var(--mf-line); padding: 8px 0; font-size: 13.5px; }
    .change strong { display: inline; } .change small { display: block; color: var(--mf-muted); }`],
  template: `
    <mf-page-header title="Resumen" crumb="ORACLE APEX" subtitle="Aplicaciones analizadas contra las notas de versión de Oracle: qué cambia en cada salto hasta la versión de destino.">
      <mf-action label="Analizar aplicaciones" icon="pi pi-search" [outlined]="!!st.latest()" [busy]="st.busy()" [disabledReason]="reason()" (run)="st.analyze()" />
    </mf-page-header>
    @if (st.error(); as e) { <p-message severity="error">{{ e.code }}: {{ e.message }}</p-message> }
    @if (st.running(); as r) {
      <section class="card"><h2>Análisis en curso</h2>
        @if (r.jobId) { <mf-job-log [jobId]="r.jobId" (finished)="st.reload()" /> }</section>
    }
    <mf-state [loading]="st.list.loading()" [error]="st.list.error()" [hasData]="!!st.list.data()" [empty]="false" (retry)="st.reload()">
      @if (st.latest(); as a) {
        <div class="cards">
          <article><b>{{ a.summary.apps }}</b>aplicaciones</article>
          <article><b>{{ a.summary.totals['Pages'] ?? 0 }}</b>páginas</article>
          <article><b>{{ a.summary.plugins }}</b>plug-ins instalados</article>
          <article><b [class.bad]="a.summary.blocked">{{ a.summary.blocked }}</b>con cambios que bloquean</article>
          <article><b>{{ a.summary.withSchema }} de {{ a.summary.apps }}</b>con su esquema completo</article>
        </div>
        @if (a.summary.withSchema < a.summary.apps) {
          <p-message severity="warn">Solo {{ a.summary.withSchema }} de {{ a.summary.apps }} aplicaciones traen el DDL de todas sus tablas en el ZIP. Para este análisis no hace falta,
            pero sin ellas una aplicación se importa y sus páginas fallan al abrirse. El detalle está en
            <a [routerLink]="['/p', st.ctx.projectId(), 'apex', 'aplicaciones']">Aplicaciones</a>.</p-message>
        }
        <section class="card">
          <h2>Plan de saltos</h2>
          <div class="controls">
            <span><label for="tg">Versión de destino</label>
              <p-select inputId="tg" [options]="targets()" [ngModel]="st.target()" (ngModelChange)="st.replan($event, st.mode())" /></span>
            <span><label for="md">Saltos</label>
              <p-select inputId="md" [options]="modes" optionLabel="label" optionValue="value" [ngModel]="st.mode()" (ngModelChange)="st.replan(st.target(), $event)" /></span>
            @if (a.summary.artifacts?.report; as id) { <mf-action label="Informe (.md)" icon="pi pi-download" [outlined]="true" (run)="api.download(id, 'analisis-apex.md')" /> }
            @if (a.summary.artifacts?.data; as id) { <mf-action label="Datos (.json)" icon="pi pi-download" [outlined]="true" (run)="api.download(id, 'analisis-apex.json')" /> }
          </div>
          @if (st.plan(); as p) {
            <p class="route" aria-label="Ruta de versiones"><span>{{ p.source }}</span>
              @for (h of p.hops; track h.version) { <i class="pi pi-arrow-right" aria-hidden="true"></i><span>{{ h.version }}</span> }</p>
            @if (!p.hops.length) { <p class="muted">La versión de destino no es posterior a la de origen.</p> }
            @if (p.baseline.length) {
              <div class="hop">
                <button type="button" (click)="toggle('base')" [attr.aria-expanded]="open() === 'base'">
                  <h3 class="grow">Antes del primer salto</h3><small class="muted">{{ p.baseline.length }} tema(s) que no dependen de una versión</small>
                  <i [class]="'pi ' + (open() === 'base' ? 'pi-chevron-up' : 'pi-chevron-down')" aria-hidden="true"></i></button>
                @if (open() === 'base') { <div class="changes">@for (c of p.baseline; track c.rule) { <ng-container *ngTemplateOutlet="row; context: { c }" /> }</div> }
              </div>
            }
            @for (h of p.hops; track h.version) {
              <div class="hop">
                <button type="button" (click)="toggle(h.version)" [attr.aria-expanded]="open() === h.version">
                  <h3 class="grow">{{ h.from }} → {{ h.version }}</h3>
                  @if (h.counts['BLOCK']) { <p-tag severity="danger" [value]="h.counts['BLOCK'] + ' bloquea'" /> }
                  @if (h.counts['REVIEW']) { <p-tag severity="warn" [value]="h.counts['REVIEW'] + ' a revisar'" /> }
                  @if (!h.changes.length) { <small class="muted">Sin cambios detectados</small> }
                  <i [class]="'pi ' + (open() === h.version ? 'pi-chevron-up' : 'pi-chevron-down')" aria-hidden="true"></i></button>
                @if (open() === h.version) { <div class="changes">@for (c of h.changes; track c.rule) { <ng-container *ngTemplateOutlet="row; context: { c }" /> }</div> }
              </div>
            }
            <p class="muted small">El análisis lee los exports contra las notas de versión de Oracle. Todavía no instala nada: importar cada aplicación en cada
              versión es la fase siguiente. APEX permite importar directo en la última versión; los saltos sirven para ver dónde cambia cada cosa.</p>
          }
        </section>
      } @else if (!st.running()) {
        <section class="card">
          <h2>{{ st.uploaded().length }} aplicaciones en el ZIP</h2>
          <p>Elija la versión de destino y pulse «Analizar aplicaciones». No se necesita base de datos.</p>
          <div class="controls"><span><label for="tg0">Versión de destino</label>
            <p-select inputId="tg0" [options]="targets()" [ngModel]="st.target()" (ngModelChange)="st.target.set($event)" /></span></div>
          <ul>@for (u of st.uploaded().slice(0, 30); track u.file) { <li>{{ u.appId }} · {{ u.name }} <small class="muted">({{ u.owner }}, {{ u.release }})</small></li> }</ul>
        </section>
      }
    </mf-state>
    <ng-template #row let-c="c">
      <div class="change"><p-tag [severity]="sev[c.severity].s" [value]="sev[c.severity].label" /> <strong>{{ c.title }}</strong>
        <small>{{ c.action }} {{ c.count }} uso(s) en {{ c.apps.length }} aplicación(es): {{ c.apps.slice(0, 6).join(', ') }}{{ c.apps.length > 6 ? '…' : '' }}.
          {{ status[c.status] }}{{ c.since ? ' desde ' + c.since : '' }}.
          @if (c.source) { <a [href]="c.source" target="_blank" rel="noopener">Nota de Oracle</a> }</small></div>
    </ng-template>`,
})
export class ApexOverview {
  st = inject(ApexStore);
  api = inject(Api);
  sev = SEVERITY;
  status = RULE_STATUS;
  modes = [{ label: 'Una versión por año (recomendado)', value: 'yearly' }, { label: 'Todas las versiones', value: 'all' }];
  open = signal<string | null>(null);
  targets = computed(() => (this.st.versions.data()?.versions ?? []).filter(v => v !== '19.1'));
  reason = computed(() => !this.st.ctx.perms() ? 'Cargando permisos…' : !this.st.ctx.can('execute') ? `Su rol (${this.st.ctx.role()}) no permite analizar`
    : this.st.running() ? 'Hay un análisis en curso' : null);
  toggle(k: string) { this.open.update(v => v === k ? null : k); }
}

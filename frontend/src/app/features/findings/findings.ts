import { DatePipe } from '@angular/common';
import { Component, computed, effect, inject, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ButtonModule } from 'primeng/button';
import { CheckboxModule } from 'primeng/checkbox';
import { DrawerModule } from 'primeng/drawer';
import { InputTextModule } from 'primeng/inputtext';
import { MessageModule } from 'primeng/message';
import { SelectModule } from 'primeng/select';
import { TableModule } from 'primeng/table';
import { TextareaModule } from 'primeng/textarea';
import { Api } from '../../core/api';
import { UiError } from '../../core/errors';
import { Finding, FindingDetail, Page, Scan } from '../../core/models';
import { ProjectContext } from '../../core/project-context';
import { CursorPager, Loader } from '../../shared/state';
import { ActionButton, PageHeader, StateView, StatusTag, TrafficLight, short } from '../../shared/ui';

const TRANSITIONS: Record<string, string[]> = {
  open: ['proposed', 'resolved', 'discarded'], proposed: ['open', 'resolved', 'discarded'], resolved: ['open'], discarded: ['open'],
};
const DECISION_LABEL: Record<string, string> = { open: 'Reabrir', proposed: 'Proponer solución', resolved: 'Resolver', discarded: 'Descartar' };

@Component({
  selector: 'mf-findings',
  imports: [DatePipe, FormsModule, ButtonModule, CheckboxModule, DrawerModule, InputTextModule, MessageModule, SelectModule, TableModule, TextareaModule,
            ActionButton, PageHeader, StateView, StatusTag, TrafficLight],
  template: `
    <mf-page-header title="Hallazgos" crumb="HALLAZGOS" subtitle="Evidencia por archivo y línea, regla, severidad y recomendación" />
    <mf-state [loading]="scans.loading()" [error]="scans.error()" [hasData]="!!scans.data()" [empty]="!scanId()"
              emptyText="No hay análisis correctos para este proyecto. Ejecute uno en Inventario." (retry)="scans.reload()">
      <form class="filters" (ngSubmit)="apply()" aria-label="Filtros de hallazgos">
        <span><label for="fs">Análisis</label>
          <p-select inputId="fs" name="scan" [options]="succeeded()" optionValue="id" [ngModel]="scanId()" (ngModelChange)="scanId.set($event); apply()">
            <ng-template #selectedItem let-s>{{ label(s) }}</ng-template><ng-template #item let-s>{{ label(s) }}</ng-template></p-select></span>
        <span><label for="sev">Severidad</label><p-select inputId="sev" name="sev" [options]="severities" optionLabel="l" optionValue="v" [(ngModel)]="f.severity" /></span>
        <span><label for="st">Estado</label><p-select inputId="st" name="st" [options]="statuses" optionLabel="l" optionValue="v" [(ngModel)]="f.status" /></span>
        <span><label for="cl">Clasificación</label><p-select inputId="cl" name="cl" [options]="classes" optionLabel="l" optionValue="v" [(ngModel)]="f.classification" /></span>
        <span><label for="rule">Regla</label><input pInputText id="rule" name="rule" [(ngModel)]="f.rule" placeholder="JMS" size="12" /></span>
        <span><label for="mod">Módulo (directorio)</label><input pInputText id="mod" name="mod" [(ngModel)]="f.module" size="14" /></span>
        <span><label for="file">Archivo contiene</label><input pInputText id="file" name="file" [(ngModel)]="f.file" size="14" /></span>
        <span class="inline"><p-checkbox inputId="blk" name="blk" [binary]="true" [(ngModel)]="f.blocking" /><label for="blk">Solo bloqueantes</label></span>
        <p-button type="submit" label="Filtrar" icon="pi pi-filter" />
        <p-button label="Limpiar" [text]="true" (onClick)="clear()" />
      </form>

      <mf-state [loading]="list.loading()" [error]="list.error()" [hasData]="!!list.data()" [empty]="list.data()?.items?.length === 0"
                emptyText="Ningún hallazgo coincide con los filtros" (retry)="list.reload()">
        <p class="muted">{{ list.data()?.total }} hallazgos. Son indicios estáticos revisables: una coincidencia textual nunca declara éxito ni compatibilidad.</p>
        <div class="panel">
          <p-table [value]="list.data()?.items ?? []" selectionMode="single" (onRowSelect)="openDetail($any($event).data)" dataKey="id">
            <ng-template #header><tr><th scope="col">Severidad</th><th scope="col">Regla</th><th scope="col">Archivo:línea</th><th scope="col">Clase</th>
              <th scope="col">Bloquea</th><th scope="col">Evidencia</th><th scope="col">Estado</th><th scope="col"></th></tr></ng-template>
            <ng-template #body let-x>
              <tr [pSelectableRow]="x">
                <td><mf-status [value]="x.severity" /></td><td><strong>{{ x.ruleId }}</strong> <small class="muted">v{{ x.ruleVersion }}</small></td>
                <td class="mono small">{{ x.file }}:{{ x.line }}</td><td><mf-light [classification]="x.classification" [blocking]="x.blocking" /> <small>{{ x.classification }}</small></td>
                <td>{{ x.blocking ? 'Sí' : 'No' }}</td><td class="mono small">{{ x.evidence }}</td><td><mf-status [value]="x.status" /></td>
                <td><p-button icon="pi pi-eye" [text]="true" ariaLabel="Ver y revisar" (onClick)="openDetail(x)" /></td>
              </tr>
            </ng-template>
          </p-table>
        </div>
        <div class="pager">
          <p-button label="Anterior" [text]="true" icon="pi pi-chevron-left" [disabled]="pager.page() === 0" (onClick)="pager.prev(); load()" />
          <span>Página {{ pager.page() + 1 }}</span>
          <p-button label="Siguiente" [text]="true" icon="pi pi-chevron-right" iconPos="right" [disabled]="!list.data()?.nextCursor" (onClick)="pager.next(list.data()?.nextCursor); load()" />
        </div>
      </mf-state>
    </mf-state>

    <p-drawer [(visible)]="drawer" position="right" [style]="{ width: '38rem' }" header="Hallazgo">
      <mf-state [loading]="detail.loading()" [error]="detail.error()" [hasData]="!!detail.data()" (retry)="detail.reload()">
        @if (detail.data(); as d) {
          <dl class="kv">
            <dt>Regla</dt><dd>{{ d.ruleId }} v{{ d.ruleVersion }} · {{ d.classification }} · <mf-status [value]="d.severity" /></dd>
            <dt>Ubicación</dt><dd class="mono">{{ d.file }}:{{ d.line }}</dd>
            <dt>Evidencia (redactada)</dt><dd class="mono">{{ d.evidence }}</dd>
            <dt>Recomendación</dt><dd>{{ d.recommendation }}</dd>
            <dt>Estado</dt><dd><mf-status [value]="d.status" /> · versión {{ d.version }} · {{ d.blocking ? 'bloqueante' : 'no bloqueante' }}</dd>
          </dl>
          <h3>Revisión</h3>
          @if (allowed().length === 0) {
            <p-message severity="info">Su rol ({{ ctx.role() }}) no puede cambiar este hallazgo en estado «{{ d.status }}».</p-message>
          } @else {
            <form class="form" (ngSubmit)="submit(d)">
              <label for="dec">Decisión</label>
              <p-select inputId="dec" name="dec" [options]="allowed()" optionLabel="l" optionValue="v" [(ngModel)]="decision" />
              <label for="reason">Motivo (obligatorio, mínimo 10 caracteres; queda en auditoría)</label>
              <textarea pTextarea id="reason" name="reason" rows="4" [(ngModel)]="reason" required minlength="10"></textarea>
              <label for="min">Minutos dedicados (opcional; calibra la estimación de esfuerzo)</label>
              <input pInputText id="min" name="min" type="number" min="1" [(ngModel)]="minutes" />
              <mf-action label="Adjuntar evidencia" icon="pi pi-paperclip" [outlined]="true" severity="secondary"
                         disabledReason="Pendiente: carga de archivos de evidencia aún no implementada; describa la evidencia en el motivo." />
              @if (reviewError(); as e) { <p-message severity="error">{{ e.conflict ? 'Conflicto: ' : '' }}{{ e.message }}</p-message> }
              <p-button type="submit" label="Registrar decisión" [disabled]="!decision || reason.trim().length < 10" [loading]="saving()" />
            </form>
          }
          <h3>Historial</h3>
          @if (d.reviews.length === 0) { <p class="muted">Sin revisiones</p> }
          <ol class="history">@for (r of d.reviews; track r.id) {
            <li><strong>{{ r.reviewer }}</strong>: {{ r.fromStatus }} → {{ r.decision }} <small class="muted">{{ r.createdAt | date: 'short' }}</small><br />{{ r.reason }}</li> }</ol>
        }
      </mf-state>
    </p-drawer>`,
})
export class Findings {
  projectId = input.required<string>();
  ctx = inject(ProjectContext);
  private api = inject(Api);
  scans = new Loader<Page<Scan>>();
  list = new Loader<Page<Finding>>();
  detail = new Loader<FindingDetail>();
  pager = new CursorPager();
  scanId = signal<string | null>(null);
  f = { severity: null as string | null, status: null as string | null, classification: null as string | null, rule: '', module: '', file: '', blocking: false };
  drawer = false;
  decision: string | null = null;
  reason = '';
  minutes: number | null = null;
  saving = signal(false);
  reviewError = signal<UiError | null>(null);
  severities = [{ l: 'Todas', v: null }, { l: 'Alta', v: 'high' }, { l: 'Media', v: 'medium' }, { l: 'Baja', v: 'low' }, { l: 'Info', v: 'info' }];
  statuses = [{ l: 'Todos', v: null }, ...['open', 'proposed', 'resolved', 'discarded'].map(v => ({ l: v, v }))];
  classes = [{ l: 'Todas', v: null }, ...['AUTO', 'AUTO_TEST', 'REVIEW', 'MANUAL'].map(v => ({ l: v, v }))];
  succeeded = computed(() => (this.scans.data()?.items ?? []).filter(s => s.status === 'succeeded'));
  allowed = computed(() => {
    const d = this.detail.data();
    if (!d) return [];
    return (TRANSITIONS[d.status] ?? []).filter(x => this.ctx.can(x === 'proposed' ? 'propose' : 'review')).map(v => ({ l: DECISION_LABEL[v], v }));
  });

  constructor() {
    effect(async () => {
      const id = this.projectId();
      this.ctx.select(id);
      const page = await this.scans.load(() => this.api.scans(id, { limit: 50 }));
      this.scanId.set(page?.items.find(s => s.status === 'succeeded')?.id ?? null);
      this.apply();
    });
  }

  label(s: Scan) { return `${new Date(s.createdAt).toLocaleString()} · ${short(s.commitSha)}`; }
  apply() { this.pager.reset(); this.load(); }
  clear() { this.f = { severity: null, status: null, classification: null, rule: '', module: '', file: '', blocking: false }; this.apply(); }
  load() {
    const id = this.scanId();
    if (!id) return;
    const f = this.f;
    this.list.load(() => this.api.findings(id, {
      severity: f.severity, status: f.status, classification: f.classification, rule: f.rule.trim().toUpperCase() || null,
      module: f.module || null, file: f.file || null, blocking: f.blocking ? true : null, limit: 25, cursor: this.pager.current(),
    }));
  }

  openDetail(x: Finding) {
    this.drawer = true;
    this.decision = null;
    this.reason = '';
    this.reviewError.set(null);
    this.detail.load(() => this.api.finding(x.id));
  }

  async submit(d: FindingDetail) {
    this.saving.set(true);
    this.reviewError.set(null);
    try {
      await this.api.reviewFinding(d.id, d.version, this.decision!, this.reason.trim(), this.minutes);
      this.minutes = null;
      this.decision = null;
      this.reason = '';
      await this.detail.reload();
      this.load();
    } catch (e) {
      this.reviewError.set(UiError.from(e));
      if (UiError.from(e).conflict) this.detail.reload();
    } finally {
      this.saving.set(false);
    }
  }
}

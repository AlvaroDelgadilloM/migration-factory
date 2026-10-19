import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { CheckboxModule } from 'primeng/checkbox';
import { IconFieldModule } from 'primeng/iconfield';
import { InputIconModule } from 'primeng/inputicon';
import { InputTextModule } from 'primeng/inputtext';
import { MessageModule } from 'primeng/message';
import { ProgressBarModule } from 'primeng/progressbar';
import { SelectButtonModule } from 'primeng/selectbutton';
import { PageHeader, StateView, StatusTag } from '../../shared/ui';
import { DataStageStore, DsAction } from './store';

/** DataStage → Airflow · Acciones: the manual work as a team worklist, grouped by kind, with progress that survives re-runs. */
@Component({
  selector: 'mf-ds-actions',
  imports: [FormsModule, CheckboxModule, IconFieldModule, InputIconModule, InputTextModule, MessageModule, ProgressBarModule, SelectButtonModule,
            PageHeader, StateView, StatusTag],
  template: `
    <mf-page-header title="Acciones manuales" crumb="DATASTAGE → AIRFLOW" subtitle="El trabajo que necesita a una persona, agrupado y con avance." />
    @if (ds.error(); as e) { <p-message severity="error">{{ e.code }}: {{ e.message }}</p-message> }
    <mf-state [loading]="ds.list.loading()" [error]="ds.list.error()" [hasData]="!!ds.list.data()" [empty]="!ds.actions().length"
              emptyText="No hay acciones pendientes (o el proyecto aún no se migra)." (retry)="ds.reload()">
      <section class="progress">
        <div><strong>{{ done() }} de {{ ds.actions().length }}</strong> resueltas ·
          <span class="danger">{{ manualPending() }} manuales pendientes</span> (su tarea no se ejecuta hasta resolverlas) ·
          {{ reviewPending() }} para revisar</div>
        <p-progressbar [value]="pct()" [showValue]="false" aria-label="Avance de las acciones" />
      </section>
      <div class="filters">
        <p-iconfield><p-inputicon styleClass="pi pi-search" />
          <input pInputText type="search" placeholder="Buscar…" [ngModel]="q()" (ngModelChange)="q.set($event)" aria-label="Buscar" /></p-iconfield>
        <p-selectbutton [options]="sevOptions" optionLabel="label" optionValue="value" [ngModel]="sev()" (ngModelChange)="sev.set($event ?? 'manual')" aria-label="Tipo" />
        <p-selectbutton [options]="showOptions" optionLabel="label" optionValue="value" [ngModel]="show()" (ngModelChange)="show.set($event ?? 'pending')" aria-label="Estado" />
      </div>
      @for (g of groups(); track g.category) {
        <details class="group" [open]="$first || groups().length <= 4">
          <summary><strong>{{ g.category }}</strong> <span class="count">{{ g.items.length }}</span></summary>
          <ul class="items">
            @for (a of g.items; track a.key) {
              <li [class.done]="isDone(a)">
                <p-checkbox [binary]="true" [ngModel]="isDone(a)" (ngModelChange)="toggle(a, $event)" [inputId]="'a-' + a.key"
                            [disabled]="!ds.ctx.can('execute')" />
                <label [for]="'a-' + a.key">
                  <span class="text">{{ a.text }}</span>
                  <span class="meta small"><mf-status [value]="a.severity" /> <span class="mono">{{ a.job }}</span> › <span class="mono">{{ a.stage }}</span>
                    @if (ds.actionState()[a.key]; as st) { @if (st.done) { · resuelta por {{ st.by }} el {{ st.at.slice(0, 10) }} } }</span>
                </label>
              </li>
            }
          </ul>
        </details>
      } @empty { <p class="empty">Ninguna acción coincide con el filtro.</p> }
      @if (!ds.ctx.can('execute')) { <p class="muted small">Su rol permite ver las acciones, no marcarlas.</p> }
    </mf-state>`,
  styles: [`.progress{background:#fff;border:1px solid var(--mf-line);border-radius:12px;padding:14px 18px;margin-bottom:14px}
    .progress p-progressbar{display:block;margin-top:8px;height:8px}.danger{color:#b42318}
    .group{background:#fff;border:1px solid var(--mf-line);border-radius:12px;padding:10px 16px;margin-bottom:10px}.group summary{cursor:pointer;padding:4px 0;display:flex;gap:10px;align-items:center}
    .count{font-size:12px;font-weight:600;background:#eef3f7;color:#536679;border-radius:999px;padding:1px 9px}
    .items{list-style:none;padding:0;margin:6px 0 0}.items li{display:flex;gap:10px;align-items:flex-start;padding:8px 0;border-top:1px solid var(--mf-line)}
    .items label{display:flex;flex-direction:column;gap:3px;cursor:pointer}.items li.done .text{text-decoration:line-through;color:var(--mf-muted)}
    .meta{color:var(--mf-muted);display:flex;gap:6px;align-items:center;flex-wrap:wrap}`],
})
export class DsActions {
  ds = inject(DataStageStore);
  q = signal('');
  sev = signal('manual');
  show = signal('pending');
  sevOptions = [{ label: 'Manuales', value: 'manual' }, { label: 'Para revisar', value: 'review' }, { label: 'Todas', value: 'all' }];
  showOptions = [{ label: 'Pendientes', value: 'pending' }, { label: 'Resueltas', value: 'done' }, { label: 'Todas', value: 'all' }];
  done = computed(() => this.ds.actions().filter(a => this.isDone(a)).length);
  manualPending = computed(() => this.ds.actions().filter(a => a.severity === 'manual' && !this.isDone(a)).length);
  reviewPending = computed(() => this.ds.actions().filter(a => a.severity === 'review' && !this.isDone(a)).length);
  pct = computed(() => this.ds.actions().length ? Math.round(100 * this.done() / this.ds.actions().length) : 0);
  groups = computed(() => {
    const q = this.q().trim().toLowerCase();
    const items = this.ds.actions().filter(a => (this.sev() === 'all' || a.severity === this.sev())
      && (this.show() === 'all' || (this.show() === 'done') === this.isDone(a))
      && (!q || `${a.job} ${a.stage} ${a.text}`.toLowerCase().includes(q)));
    const by = new Map<string, DsAction[]>();
    for (const a of items) by.set(a.category, [...(by.get(a.category) ?? []), a]);
    return [...by.entries()].map(([category, its]) => ({ category, items: its })).sort((a, b) => b.items.length - a.items.length);
  });
  isDone(a: DsAction) { return !!this.ds.actionState()[a.key]?.done; }
  toggle(a: DsAction, done: boolean) { this.ds.setAction(a.key, done); }
}

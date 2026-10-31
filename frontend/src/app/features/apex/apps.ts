import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { SelectButtonModule } from 'primeng/selectbutton';
import { TableModule } from 'primeng/table';
import { TagModule } from 'primeng/tag';
import { ApexApp } from '../../core/models';
import { PageHeader, StateView } from '../../shared/ui';
import { RULE_STATUS, SEVERITY } from './overview';
import { ApexStore } from './store';

const APP_STATE: Record<string, { label: string; s: 'danger' | 'warn' | 'success' }> = {
  BLOCK: { label: 'Bloquea', s: 'danger' }, REVIEW: { label: 'Revisar', s: 'warn' }, OK: { label: 'Sin cambios', s: 'success' },
};

/** Oracle APEX · Aplicaciones: each application, its plug-ins, what must change and how much of its schema the ZIP brings. */
@Component({
  selector: 'mf-apex-apps',
  imports: [FormsModule, SelectButtonModule, TableModule, TagModule, PageHeader, StateView],
  styles: [`
    .panel { background: #fff; border: 1px solid var(--mf-line); border-radius: 12px; overflow-x: auto; margin-bottom: 16px; }
    .card { background: #fff; border: 1px solid var(--mf-line); border-radius: 12px; padding: 14px 18px; margin: 0 0 16px; }
    tr.row { cursor: pointer; } tr.row.on { background: var(--mf-teal-soft); } h2 { font-size: 17px; margin: 0 0 4px; } h3 { font-size: 14.5px; margin: 14px 0 6px; }
    .filters { margin: 0 0 12px; } .facts { display: flex; flex-wrap: wrap; gap: 6px 18px; font-size: 13.5px; margin: 0 0 8px; } .num { text-align: right; }
    .f { border-top: 1px solid var(--mf-line); padding: 8px 0; font-size: 13.5px; } .f small { display: block; color: var(--mf-muted); }
    ul.tight { margin: 0; padding-left: 18px; font-size: 13.5px; } code { font-size: 12.5px; }`],
  template: `
    <mf-page-header title="Aplicaciones" crumb="ORACLE APEX" subtitle="Cada aplicación del ZIP: su tamaño, sus plug-ins, lo que debe cambiar y cuánto de su esquema viene incluido." />
    <mf-state [loading]="st.list.loading()" [error]="st.list.error()" [hasData]="!!st.list.data()" [empty]="!st.latest()"
              emptyText="Aún no hay análisis. Ejecútelo desde Resumen." (retry)="st.reload()">
      <div class="filters"><p-selectbutton [options]="filters" optionLabel="label" optionValue="value" [ngModel]="filter()" (ngModelChange)="filter.set($event ?? 'all')" aria-label="Filtrar aplicaciones" /></div>
      <div class="panel">
        <p-table [value]="shown()" dataKey="file" [tableStyle]="{ 'min-width': '52rem' }">
          <ng-template #header><tr><th scope="col">Aplicación</th><th scope="col">Dueño</th><th scope="col">Versión</th><th scope="col" class="num">Páginas</th>
            <th scope="col" class="num">Plug-ins</th><th scope="col" class="num">Bloquean</th><th scope="col" class="num">Revisar</th><th scope="col">Objetos con DDL</th><th scope="col">Estado</th></tr></ng-template>
          <ng-template #body let-a><tr class="row" [class.on]="selected()?.file === a.file" (click)="selected.set(a)" tabindex="0" (keydown.enter)="selected.set(a)">
            <td><strong>{{ a.appId }}</strong> · {{ a.name }}</td><td>{{ a.owner }}</td><td>{{ a.version }}</td><td class="num">{{ a.stats['Pages'] ?? 0 }}</td>
            <td class="num">{{ a.plugins.length }}</td><td class="num">{{ a.counts.BLOCK }}</td><td class="num">{{ a.counts.REVIEW }}</td>
            <td>{{ a.schema.defined }} de {{ a.schema.referenced }}</td><td><p-tag [severity]="state[a.status].s" [value]="state[a.status].label" /></td></tr></ng-template>
        </p-table>
      </div>
      @if (selected(); as a) {
        <section class="card">
          <h2>{{ a.appId }} · {{ a.name }}</h2>
          <p class="facts"><span>Archivo <code>{{ a.file }}</code></span><span>Modo de compatibilidad {{ a.compatibilityMode ?? '—' }}</span><span>Tema {{ a.theme ?? '—' }}</span>
            <span>Autenticación {{ a.authentication.join(', ') || '—' }}</span>
            @for (k of statKeys; track k[0]) { <span>{{ k[1] }} {{ a.stats[k[0]] ?? 0 }}</span> }</p>
          <h3>Qué debe cambiar</h3>
          @for (f of a.findings; track f.rule) {
            <div class="f"><p-tag [severity]="sev[f.severity].s" [value]="sev[f.severity].label" /> <strong>{{ f.title }}</strong> · {{ f.count }} uso(s)
              <small>{{ f.action }} {{ status[f.status] }}@if (f.since) { desde {{ f.since }} }.
                @if (f.source) { <a [href]="f.source" target="_blank" rel="noopener">Nota de Oracle</a> }</small>
              <small>Dónde: {{ f.where.join(', ') }}{{ f.locations > f.where.length ? ' y más' : '' }}</small></div>
          } @empty { <p class="muted">Sin cambios detectados.</p> }
          <h3>Plug-ins ({{ a.plugins.length }})</h3>
          @if (a.plugins.length) {
            <p class="muted small">Son de terceros: hay que confirmar con cada autor que exista versión para el destino.</p>
            <ul class="tight">@for (p of a.plugins; track p.name) { <li>{{ p.displayName }} <small class="muted">({{ p.type }}, versión {{ p.version ?? '—' }}, {{ p.uses }} uso(s))</small></li> }</ul>
          } @else { <p class="muted">No usa plug-ins.</p> }
          <h3>Esquema</h3>
          <p class="small">Usa {{ a.schema.referenced }} objetos de los esquemas {{ a.schema.schemas.join(', ') || '—' }}; el ZIP trae el DDL de {{ a.schema.defined }}.</p>
          @if (a.schema.missingSample.length) { <p class="small muted">Faltan, por ejemplo: {{ a.schema.missingSample.join(', ') }}.</p> }
          @if (a.hosts.length) { <h3>Sitios que menciona</h3><p class="small muted">{{ a.hosts.join(', ') }}</p> }
        </section>
      }
    </mf-state>`,
})
export class ApexApps {
  st = inject(ApexStore);
  sev = SEVERITY;
  status = RULE_STATUS;
  state = APP_STATE;
  statKeys = [['Items', 'Campos'], ['Processes', 'Procesos'], ['Regions', 'Regiones'], ['Dynamic Actions', 'Acciones dinámicas']];
  filters = [{ label: 'Todas', value: 'all' }, { label: 'Bloquean', value: 'BLOCK' }, { label: 'Revisar', value: 'REVIEW' }];
  filter = signal('all');
  selected = signal<ApexApp | null>(null);
  shown = computed(() => {
    const apps = this.st.latest()?.apps ?? [];
    return this.filter() === 'all' ? apps : apps.filter(a => a.status === this.filter());
  });
}

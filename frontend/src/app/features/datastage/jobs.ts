import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { DrawerModule } from 'primeng/drawer';
import { IconFieldModule } from 'primeng/iconfield';
import { InputIconModule } from 'primeng/inputicon';
import { InputTextModule } from 'primeng/inputtext';
import { SelectButtonModule } from 'primeng/selectbutton';
import { TableModule } from 'primeng/table';
import { PageHeader, StateView, StatusTag } from '../../shared/ui';
import { DataStageStore, DsJob } from './store';
import { FlowDiagram } from './flow-diagram';

/** DataStage → Airflow · Jobs: full-width table with filters; a click opens the job's detail beside the list. */
@Component({
  selector: 'mf-ds-jobs',
  imports: [FormsModule, DrawerModule, IconFieldModule, InputIconModule, InputTextModule, SelectButtonModule, TableModule, PageHeader, StateView, StatusTag, FlowDiagram],
  template: `
    <mf-page-header title="Jobs" crumb="DATASTAGE → AIRFLOW" subtitle="Cada job de DataStage, su complejidad y lo que se generó para Airflow." />
    <mf-state [loading]="ds.list.loading()" [error]="ds.list.error()" [hasData]="!!ds.list.data()" [empty]="!ds.jobs().length"
              emptyText="Sin resultados todavía: migre el proyecto desde Resumen." (retry)="ds.reload()">
      <div class="filters">
        <p-iconfield><p-inputicon styleClass="pi pi-search" />
          <input pInputText type="search" placeholder="Buscar job" [ngModel]="q()" (ngModelChange)="q.set($event)" aria-label="Buscar job" /></p-iconfield>
        <p-selectbutton [options]="stateOptions()" optionLabel="label" optionValue="value" [ngModel]="state()" (ngModelChange)="state.set($event ?? 'ALL')"
                        aria-label="Filtrar por resultado" />
        <p-selectbutton [options]="typeOptions" optionLabel="label" optionValue="value" [ngModel]="type()" (ngModelChange)="type.set($event ?? 'ALL')"
                        aria-label="Filtrar por tipo" />
      </div>
      <div class="panel">
        <p-table [value]="rows()" dataKey="name" sortField="name" [sortOrder]="1" selectionMode="single" [(selection)]="selected"
                 (onRowSelect)="open.set(true)" [rowHover]="true" [tableStyle]="{ 'min-width': '60rem' }">
          <ng-template #header><tr>
            <th scope="col" pSortableColumn="name">Job <p-sortIcon field="name" /></th><th scope="col">Tipo</th>
            <th scope="col" pSortableColumn="score">Complejidad <p-sortIcon field="score" /></th>
            <th scope="col">Resultado</th><th scope="col" pSortableColumn="pending">Acciones <p-sortIcon field="pending" /></th>
            <th scope="col">Confianza</th></tr></ng-template>
          <ng-template #body let-r>
            <tr [pSelectableRow]="r" class="clickable">
              <td><span class="mono">{{ r.name }}</span>@if (ds.pilot()[r.name]) { <span class="chip">piloto</span> }</td>
              <td><mf-status [value]="r.jobType" /></td>
              <td><mf-status [value]="r.complexity" /><small class="muted pts">{{ r.score }} puntos</small></td>
              <td><mf-status [value]="r.state" /></td>
              <td>@if (r.pending) { <strong>{{ r.pending }}</strong> pendiente(s) } @else { <span class="muted">—</span> }</td>
              <td class="small">{{ conf(r.confidence) }}</td>
            </tr>
          </ng-template>
          <ng-template #emptymessage><tr><td colspan="6" class="muted">Ningún job coincide con el filtro.</td></tr></ng-template>
        </p-table>
      </div>
      <p class="muted small">{{ rows().length }} de {{ ds.jobs().length }} jobs. Haga clic en una fila para ver etapas, SQL generado y dependencias.</p>
    </mf-state>

    <p-drawer [(visible)]="openFlag" position="right" [style]="{ width: selected?.flow ? 'min(78rem, 98vw)' : 'min(46rem, 96vw)' }" [header]="selected?.name ?? ''">
      @if (selected; as j) {
        <p><mf-status [value]="j.state" /> <mf-status [value]="j.jobType" /> <mf-status [value]="j.complexity" />
          <span class="muted small"> · {{ j.stages }} etapas · {{ j.sourceFile }}</span></p>
        @if (j.dag) { <p class="small">DAG: <code>{{ j.dag }}</code></p> }
        @if (j.flow; as f) {
          <h3>Secuencia de DataStage → DAG de Airflow</h3>
          <mf-flow-diagram [flow]="f" />
        }
        <h3>Por qué esta complejidad</h3>
        <ul class="small">@for (x of j.reasons; track x) { <li>{{ x }}</li> }</ul>
        @if (j.dependsOn.length || j.calls.length) {
          <h3>Dependencias</h3>
          @if (j.calls.length) { <p class="small"><strong>Llama a:</strong> @for (c of j.calls; track c) { <span class="chip mono">{{ c }}</span> }</p> }
          @if (j.dependsOn.length) { <p class="small"><strong>Depende de:</strong> @for (c of j.dependsOn; track c) { <span class="chip mono">{{ c }}</span> }</p> }
        }
        @if (j.steps.length) {
          <h3>Pasos generados</h3>
          <table class="inner"><thead><tr><th>Tarea</th><th>Etapa</th><th>Estado</th><th>SQL</th></tr></thead><tbody>
            @for (s of j.steps; track s.task) {
              <tr><td class="mono small">{{ s.task }}</td><td class="mono small">{{ s.stage }}</td>
                <td><span [class]="'st st-' + s.status">{{ stepLabel(s.status) }}</span></td><td class="mono small">{{ s.sqlFile ?? '—' }}</td></tr>
              @if (s.issues.length) { <tr><td colspan="4" class="small muted issue">↳ {{ s.issues.join(' · ') }}</td></tr> }
            }
          </tbody></table>
        }
        @if (j.manualActions.length) {
          <h3>Acciones de mapeo</h3><ul class="small">@for (a of j.manualActions; track a) { <li>{{ a }}</li> }</ul>
        }
      }
    </p-drawer>`,
  styles: [`.clickable{cursor:pointer}.pts{display:block;margin-top:3px}.st{font-size:12px;padding:1px 8px;border-radius:10px}.st-OK{background:#e7f6ec;color:#067647}
    .st-REVIEW{background:#e6f2f3;color:#0a5f73}.st-MANUAL{background:#fdecea;color:#b42318}.issue{padding-left:16px}h3{margin:16px 0 6px;font-size:15px}`],
})
export class DsJobs {
  ds = inject(DataStageStore);
  q = signal('');
  state = signal('ALL');
  type = signal('ALL');
  open = signal(false);
  selected: (DsJob & { pending: number }) | null = null;
  get openFlag() { return this.open(); }
  set openFlag(v: boolean) { this.open.set(v); }
  typeOptions = [{ label: 'Todos', value: 'ALL' }, { label: 'Jobs', value: 'PARALLEL' }, { label: 'Secuencias', value: 'SEQUENCE' }];
  stateOptions = computed(() => {
    const c = (s: string) => this.ds.jobs().filter(j => j.state === s).length;
    return [{ label: `Todos (${this.ds.jobs().length})`, value: 'ALL' }, { label: `Trabajo manual (${c('MANUAL_REVIEW')})`, value: 'MANUAL_REVIEW' },
      { label: `Revisar (${c('SEMIAUTOMATIC')})`, value: 'SEMIAUTOMATIC' }, { label: `Automático (${c('AUTO_MIGRATED')})`, value: 'AUTO_MIGRATED' }];
  });
  rows = computed(() => {
    const q = this.q().trim().toLowerCase(), st = this.ds.actionState();
    return this.ds.jobs()
      .filter(j => (this.state() === 'ALL' || j.state === this.state()) && (this.type() === 'ALL' || j.jobType === this.type()))
      .filter(j => !q || j.name.toLowerCase().includes(q))
      .map(j => ({ ...j, pending: this.ds.actions().filter(a => a.job === j.name && a.severity === 'manual' && !st[a.key]?.done).length }));
  });
  conf(c: string) { return ({ HIGH: 'Alta', MEDIUM: 'Media', LOW: 'Baja' } as Record<string, string>)[c] ?? c; }
  stepLabel(s: string) { return ({ OK: 'Listo', REVIEW: 'Revisar', MANUAL: 'Manual' } as Record<string, string>)[s] ?? s; }
}

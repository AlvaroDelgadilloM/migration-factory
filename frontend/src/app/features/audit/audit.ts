import { Component, inject, signal } from '@angular/core';
import { DatePipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ButtonModule } from 'primeng/button';
import { InputTextModule } from 'primeng/inputtext';
import { SelectModule } from 'primeng/select';
import { TableModule } from 'primeng/table';
import { Api } from '../../core/api';
import { UiError } from '../../core/errors';
import { AuditEvent, Page, Project } from '../../core/models';
import { CursorPager, Loader } from '../../shared/state';
import { PageHeader, StateView } from '../../shared/ui';

@Component({
  selector: 'mf-audit',
  imports: [DatePipe, FormsModule, ButtonModule, InputTextModule, SelectModule, TableModule, PageHeader, StateView],
  template: `
    <mf-page-header title="Auditoría" crumb="AUDITORÍA" subtitle="Actor, evento, entidad y huellas antes/después">
      <p-button label="Exportar CSV" icon="pi pi-download" [outlined]="true" (onClick)="export()" />
    </mf-page-header>
    @if (exportError(); as e) { <p class="err">{{ e.message }}</p> }
    <form class="filters" (ngSubmit)="apply()">
      <span><label for="pr">Proyecto</label><p-select inputId="pr" name="pr" [options]="projects()" optionLabel="name" optionValue="id" [(ngModel)]="projectId" placeholder="Todos los permitidos" [showClear]="true" /></span>
      <span><label for="ac">Acción (prefijo)</label><input pInputText id="ac" name="ac" [(ngModel)]="action" placeholder="finding." /></span>
      <span><label for="et">Entidad</label><input pInputText id="et" name="et" [(ngModel)]="entityType" placeholder="plan" /></span>
      <p-button type="submit" label="Filtrar" icon="pi pi-filter" />
    </form>
    <mf-state [loading]="list.loading()" [error]="list.error()" [hasData]="!!list.data()" [empty]="list.data()?.items?.length === 0"
              emptyText="Sin eventos visibles (la auditoría se muestra a owner, arquitecto y auditor del proyecto)." (retry)="list.reload()">
      <p-table [value]="list.data()?.items ?? []">
        <ng-template #header><tr><th scope="col">Fecha</th><th scope="col">Actor</th><th scope="col">Acción</th><th scope="col">Entidad</th><th scope="col">Antes → después</th><th scope="col">Detalle</th><th scope="col">requestId</th></tr></ng-template>
        <ng-template #body let-a><tr><td>{{ a.at | date: 'medium' }}</td><td>{{ a.actor }}</td><td><code>{{ a.action }}</code></td>
          <td>{{ a.entityType }} <small class="mono muted">{{ a.entityId }}</small></td>
          <td class="mono small">{{ a.beforeHash?.slice(0, 8) ?? '—' }} → {{ a.afterHash?.slice(0, 8) ?? '—' }}</td>
          <td class="small">{{ detail(a) }}</td><td class="mono small">{{ a.requestId?.slice(0, 12) }}</td></tr></ng-template>
      </p-table>
      <div class="pager">
        <p-button label="Anterior" [text]="true" icon="pi pi-chevron-left" [disabled]="pager.page() === 0" (onClick)="pager.prev(); load()" />
        <span>{{ list.data()?.total }} eventos · página {{ pager.page() + 1 }}</span>
        <p-button label="Siguiente" [text]="true" icon="pi pi-chevron-right" iconPos="right" [disabled]="!list.data()?.nextCursor" (onClick)="pager.next(list.data()?.nextCursor); load()" />
      </div>
    </mf-state>`,
})
export class Audit {
  private api = inject(Api);
  list = new Loader<Page<AuditEvent>>();
  pager = new CursorPager();
  projects = signal<Project[]>([]);
  projectId: string | null = null;
  action = '';
  entityType = '';
  exportError = signal<UiError | null>(null);

  constructor() {
    this.api.projects({ limit: 200 }).then(p => this.projects.set(p.items)).catch(() => undefined);
    this.load();
  }

  apply() { this.pager.reset(); this.load(); }
  load() {
    this.list.load(() => this.api.audit({ projectId: this.projectId, action: this.action || null, entityType: this.entityType || null,
                                          limit: 50, cursor: this.pager.current() }));
  }
  detail(a: AuditEvent) { return Object.entries(a.details ?? {}).map(([k, v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v) : v}`).join(' · ').slice(0, 200); }
  async export() {
    this.exportError.set(null);
    try { await this.api.exportAudit(this.projectId ?? undefined); } catch (e) { this.exportError.set(UiError.from(e)); }
  }
}

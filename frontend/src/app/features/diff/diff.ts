import { Component, computed, effect, inject, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MessageModule } from 'primeng/message';
import { SelectModule } from 'primeng/select';
import { TableModule } from 'primeng/table';
import { InputTextModule } from 'primeng/inputtext';
import { TextareaModule } from 'primeng/textarea';
import { Api } from '../../core/api';
import { UiError } from '../../core/errors';
import { DiffFile, DiffFileDetail, Execution, Page } from '../../core/models';
import { ProjectContext } from '../../core/project-context';
import { CursorPager, Loader } from '../../shared/state';
import { ActionButton, PageHeader, StateView, StatusTag } from '../../shared/ui';
import { executionLabel } from '../execution/execution';

@Component({
  selector: 'mf-diff',
  imports: [FormsModule, InputTextModule, MessageModule, SelectModule, TableModule, TextareaModule, ActionButton, PageHeader, StateView, StatusTag],
  template: `
    <mf-page-header title="Diff" crumb="DIFF" subtitle="Cambios por archivo, receta de origen y revisión" />
    <mf-state [loading]="list.loading()" [error]="list.error()" [hasData]="!!list.data()" [empty]="list.data()?.items?.length === 0"
              emptyText="Sin ejecuciones con diff." (retry)="list.reload()">
      <div class="toolbar">
        <label for="ex">Ejecución</label>
        <p-select inputId="ex" [options]="list.data()?.items ?? []" optionValue="id" [ngModel]="selected()" (ngModelChange)="selected.set($event)" [style]="{ minWidth: '26rem' }">
          <ng-template #selectedItem let-e>{{ label(e) }}</ng-template><ng-template #item let-e>{{ label(e) }}</ng-template></p-select>
      </div>
      @if (exec(); as e) {
        @if (e.mode === 'preview') { <p-message severity="info">Diff de preview (dryRun): solo lectura. La revisión por archivo aplica a ejecuciones.</p-message> }
        @if (e.rolledBack) { <p-message severity="warn">Candidato descartado: el diff se conserva como evidencia.</p-message> }
      }
      <div class="split">
        <section class="files" aria-label="Archivos modificados">
          <mf-state [loading]="files.loading()" [error]="files.error()" [hasData]="!!files.data()" [empty]="files.data()?.items?.length === 0"
                    emptyText="La ejecución no produjo cambios" (retry)="files.reload()">
            <p-table [value]="files.data()?.items ?? []" selectionMode="single" [(selection)]="current" (onRowSelect)="openFile($any($event).data)" dataKey="id">
              <ng-template #header><tr><th scope="col">Archivo</th><th scope="col">+/-</th><th scope="col">Revisión</th></tr></ng-template>
              <ng-template #body let-f><tr [pSelectableRow]="f">
                <td class="mono small">{{ f.path }}<br /><small class="muted">{{ f.changeType }} · {{ f.recipes.join(', ') || 'sin atribución' }}</small></td>
                <td><span class="add">+{{ f.additions }}</span> <span class="del">-{{ f.deletions }}</span></td><td><mf-status [value]="f.reviewStatus" /></td></tr></ng-template>
            </p-table>
            <div class="pager">
              <button type="button" class="link" [disabled]="pager.page() === 0" (click)="pager.prev(); loadFiles()">← Anterior</button>
              <span>{{ files.data()?.total }} archivos</span>
              <button type="button" class="link" [disabled]="!files.data()?.nextCursor" (click)="pager.next(files.data()?.nextCursor); loadFiles()">Siguiente →</button>
            </div>
          </mf-state>
        </section>
        <section class="patch" aria-label="Contenido del cambio">
          <mf-state [loading]="file.loading()" [error]="file.error()" [hasData]="!!file.data()" [empty]="!file.data() && !file.loading()"
                    emptyText="Seleccione un archivo (se carga por archivo)" (retry)="file.reload()">
            @if (file.data(); as d) {
              <h3 class="mono">{{ d.path }}</h3>
              <p class="muted">Receta(s): {{ d.recipes.join(', ') || '—' }} · estado <mf-status [value]="d.reviewStatus" />
                @if (d.reviewedBy) { · por {{ d.reviewedBy }} } @if (d.reviewReason) { — {{ d.reviewReason }} }</p>
              <pre class="diff" [attr.aria-label]="'Diff de ' + d.path">@for (l of lines(); track $index) {<span [class]="l.c">{{ l.t }}</span>
}</pre>
              <div class="review">
                <label for="rr">Motivo (obligatorio para rechazar)</label>
                <textarea pTextarea id="rr" rows="2" [(ngModel)]="reason"></textarea>
                <label for="dm">Minutos dedicados a este archivo (opcional)</label>
                <input pInputText id="dm" type="number" min="1" [(ngModel)]="minutes" />
                <span class="actions">
                  <mf-action label="Aprobar archivo" icon="pi pi-check" severity="success" [disabledReason]="reviewReason()" (run)="review(d, 'approved')" />
                  <mf-action label="Rechazar archivo" icon="pi pi-times" severity="danger" [outlined]="true"
                             [disabledReason]="reviewReason() ?? (reason.trim().length < 10 ? 'Escriba un motivo de al menos 10 caracteres' : null)" (run)="review(d, 'rejected')" />
                </span>
                @if (error(); as e) { <p-message severity="error">{{ e.code }}: {{ e.message }}</p-message> }
              </div>
            }
          </mf-state>
        </section>
      </div>
    </mf-state>`,
})
export class DiffPage {
  projectId = input.required<string>();
  ctx = inject(ProjectContext);
  private api = inject(Api);
  list = new Loader<Page<Execution>>();
  files = new Loader<Page<DiffFile>>();
  file = new Loader<DiffFileDetail>();
  pager = new CursorPager();
  selected = signal<string | null>(null);
  current: DiffFile | null = null;
  reason = '';
  minutes: number | null = null;
  error = signal<UiError | null>(null);
  label = executionLabel;
  exec = computed(() => this.list.data()?.items.find(e => e.id === this.selected()) ?? null);
  lines = computed(() => (this.file.data()?.patch ?? '').split('\n').map(t => ({
    t, c: t.startsWith('+') && !t.startsWith('+++') ? 'add' : t.startsWith('-') && !t.startsWith('---') ? 'del' : t.startsWith('@@') ? 'hunk' : '',
  })));
  reviewReason = computed(() => {
    const e = this.exec();
    if (!this.ctx.can('approve_diff')) return `Su rol (${this.ctx.role()}) no permite revisar diffs`;
    if (!e || e.mode !== 'apply') return 'Solo se revisan diffs de ejecuciones (no de preview)';
    if (e.state !== 'succeeded' || e.rolledBack) return 'La ejecución no está terminada o fue descartada';
    return null;
  });

  constructor() {
    effect(async () => {
      const id = this.projectId();
      this.ctx.select(id);
      const page = await this.list.load(() => this.api.executions(id, { limit: 50 }));
      this.selected.set(page?.items[0]?.id ?? null);
    });
    effect(() => { if (this.selected()) { this.pager.reset(); this.file.data.set(null); this.loadFiles(); } });
  }

  loadFiles() { const id = this.selected(); if (id) this.files.load(() => this.api.diff(id, { limit: 50, cursor: this.pager.current() })); }
  openFile(f: DiffFile) { this.error.set(null); this.reason = ''; this.file.load(() => this.api.diffFile(f.id)); }

  async review(d: DiffFileDetail, decision: 'approved' | 'rejected') {
    this.error.set(null);
    try {
      await this.api.reviewDiff(d.id, d.version, decision, this.reason.trim(), this.minutes);
      this.minutes = null;
      await this.file.reload();
      this.files.reload();
    } catch (e) {
      this.error.set(UiError.from(e));
    }
  }
}

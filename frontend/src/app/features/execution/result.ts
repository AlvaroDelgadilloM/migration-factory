import { Component, effect, inject, input, signal } from '@angular/core';
import { MessageModule } from 'primeng/message';
import { Api } from '../../core/api';
import { UiError } from '../../core/errors';
import { Execution, ExecutionResult } from '../../core/models';
import { ProjectContext } from '../../core/project-context';
import { Loader } from '../../shared/state';
import { ActionButton, StateView } from '../../shared/ui';

/** Doc 19 part B: LOCAL -> download the migrated project; GIT_REMOTE -> also a pull request, only through the PR gate. */
@Component({
  selector: 'mf-result',
  imports: [MessageModule, ActionButton, StateView],
  template: `
    <mf-state [loading]="res.loading()" [error]="res.error()" [hasData]="!!res.data()" (retry)="res.reload()">
      @if (res.data(); as r) {
        <p class="muted">Origen <strong>{{ r.sourceType === 'LOCAL' ? 'local (carpeta o ZIP)' : 'Git remoto' }}</strong> · estado {{ r.status }}</p>
        @if (r.download.warning) { <p-message [severity]="r.download.kind === 'project' ? 'warn' : 'error'">{{ r.download.warning }}</p-message> }
        <div class="actions">
          @if (r.download.available) {
            <mf-action [label]="r.download.label" icon="pi pi-download" (run)="downloadZip(r)" />
          }
          @if (r.artifacts.report) { <mf-action label="Descargar reporte" icon="pi pi-file" severity="secondary" [outlined]="true" (run)="get(r.artifacts.report!, 'execution-report.html')" /> }
          @if (r.artifacts.diff) { <mf-action label="Descargar diff" icon="pi pi-code" severity="secondary" [outlined]="true" (run)="get(r.artifacts.diff!, 'diff.patch')" /> }
          @if (r.artifacts.manualActions) { <mf-action [label]="r.download.kind === 'analysis' ? 'Descargar acciones requeridas' : 'Descargar acciones manuales'" icon="pi pi-list-check" severity="secondary" [outlined]="true" (run)="get(r.artifacts.manualActions!, 'manual-actions.md')" /> }
          @if (r.artifacts.camelComponents) { <mf-action label="Componentes Camel" icon="pi pi-sitemap" severity="secondary" [outlined]="true" (run)="get(r.artifacts.camelComponents!, 'camel-component-migration-report.md')" /> }
          @if (r.pullRequest.shown) {
            <mf-action label="Crear Pull Request" icon="pi pi-github" [disabledReason]="prReason(r)" (run)="createPr(r)" />
          }
        </div>
        @if (r.pullRequest.shown && !r.pullRequestAvailable && r.pullRequest.reasons.length) {
          <p-message severity="info"><div><strong>PR no disponible todavía:</strong><ul>@for (x of r.pullRequest.reasons; track x) { <li>{{ x }}</li> }</ul></div></p-message>
        }
        @if (msg()) { <p class="muted">{{ msg() }}</p> }
        @if (error(); as e) { <p-message severity="error">{{ e.code }}: {{ e.message }}</p-message> }
      }
    </mf-state>`,
  styles: [`.actions{display:flex;flex-wrap:wrap;gap:.5rem;margin:.5rem 0}`],
})
export class ResultPanel {
  execution = input.required<Execution>();
  private api = inject(Api);
  ctx = inject(ProjectContext);
  res = new Loader<ExecutionResult>();
  msg = signal('');
  error = signal<UiError | null>(null);

  constructor() { effect(() => { const id = this.execution().id; this.execution().state; this.res.load(() => this.api.executionResult(id)); }); }

  downloadZip(r: ExecutionResult) {
    this.api.downloadResult(r.executionId, r.download.kind === 'project' ? 'migrated-project.zip' : 'candidate-diagnostico.zip');
  }
  get(id: string, name: string) { this.api.download(id, name); }
  prReason(r: ExecutionResult) {
    if (!this.ctx.can('pull_request')) return `Su rol (${this.ctx.role()}) no permite crear PR`;
    return r.pullRequestAvailable ? null : 'Bloqueado por el PR gate (ver motivos)';
  }
  async createPr(r: ExecutionResult) {
    this.error.set(null);
    try { await this.api.pullRequest(r.executionId); this.msg.set('Creación del PR en cola; el progreso aparece en los logs.'); }
    catch (e) { this.error.set(UiError.from(e)); }
  }
}

import { Component, computed, effect, inject, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MessageModule } from 'primeng/message';
import { SelectModule } from 'primeng/select';
import { TableModule } from 'primeng/table';
import { TextareaModule } from 'primeng/textarea';
import { Api } from '../../core/api';
import { UiError } from '../../core/errors';
import { Profile, WorkspaceMigration } from '../../core/models';
import { ProjectContext } from '../../core/project-context';
import { Loader } from '../../shared/state';
import { ActionButton, JobLog, StateView, StatusTag } from '../../shared/ui';

/** Doc 18 on the platform: migrate every project of the workspace by waves (dependencies first). */
@Component({
  selector: 'mf-ws-migration',
  imports: [FormsModule, MessageModule, SelectModule, TableModule, TextareaModule, ActionButton, JobLog, StateView, StatusTag],
  template: `
    <h3>Migración por oleadas</h3>
    <p class="muted">Migra todos los proyectos del workspace en orden de dependencias. Si un proyecto no migra, los que dependen de él
      quedan <code>BLOCKED</code> (DEPENDENCY_MIGRATION_FAILED); el resto continúa. Cada proyecto se construye y prueba contra la versión
      legacy (línea base) o migrada (candidato) de sus dependencias internas.</p>
    <div class="form">
      <span><label for="wsp">Perfil</label>
        <p-select inputId="wsp" [options]="profiles.data() ?? []" optionLabel="name" optionValue="id" [(ngModel)]="profileId" [style]="{ minWidth: '22rem' }" /></span>
      <span><label for="wsj">Java</label><p-select inputId="wsj" [options]="['17', '21']" [(ngModel)]="java" /></span>
    </div>
    <label for="wsa">Decisiones del arquitecto sobre hallazgos bloqueantes, una por línea: <code>REGLA=motivo</code> (se aplican a todos los proyectos y quedan en auditoría)</label>
    <textarea pTextarea id="wsa" rows="2" [(ngModel)]="acceptText" placeholder="CAMEL2_VERSION=Revisado contra la guía 2→3 para todo el workspace"></textarea>
    @if (error(); as e) { <p-message severity="error">{{ e.code }}: {{ e.message }}</p-message> }
    <mf-action label="Migrar workspace por oleadas" icon="pi pi-sitemap" [busy]="busy()" [disabledReason]="reason()" (run)="start()" />

    <mf-state [loading]="list.loading()" [error]="list.error()" [hasData]="!!list.data()" [empty]="list.data()?.length === 0"
              emptyText="Sin migraciones de workspace para este análisis." (retry)="list.reload()">
      @if (current(); as m) {
        <p><mf-status [value]="m.state" /> {{ m.summary['workspaceStatus'] ?? '' }} · {{ m.summary['analysisStatus'] ?? '' }} · {{ m.summary['migrationStatus'] ?? '' }}
          · por {{ m.createdBy }} · Java {{ m.java }}@if (m.retryOf) { · reintento de {{ m.retryOf.slice(0, 8) }}@if (m.retry.length) { ({{ m.retry.join(', ') }}) } }</p>
        @if (migrationWaves(m).length) {
          <h4>Migración por oleadas</h4>
          <div class="waves">@for (w of migrationWaves(m); track w.wave) {
            <div class="wave"><strong>Wave {{ w.wave }}</strong> <small class="muted">{{ w.status }}</small>
              <ul>@for (n of w.projects; track n) { <li><span [class]="cls(r(m, n)?.status)" aria-hidden="true">{{ glyph(r(m, n)?.status) }}</span> {{ n }}
                <small class="muted">{{ r(m, n)?.status }}@if (r(m, n)?.reused) { · reutilizado }</small>
                @if (r(m, n)?.blockedBy?.length) { <br /><small class="bad">Bloqueado por {{ r(m, n).blockedBy.join(', ') }}</small> }</li> }</ul></div> }
          </div>
          @if (!['queued', 'running'].includes(m.state) && retryable(m).length) {
            <div class="retry">
              @for (n of retryable(m); track n) { <mf-action [label]="'Reintentar ' + n" icon="pi pi-replay" [outlined]="true" severity="secondary" [disabledReason]="reason()" (run)="retry(m, [n])" /> }
              <mf-action label="Reanudar (todos los no exitosos)" icon="pi pi-forward" [outlined]="true" [disabledReason]="reason()" (run)="retry(m, [])" />
            </div>
          }
        }
        @if (m.jobId && ['queued', 'running'].includes(m.state)) { <mf-job-log [jobId]="m.jobId" (finished)="list.reload()" /> }
        @if (m.job?.errorMessage) { <p-message severity="warn">{{ m.job?.errorCode }}: {{ m.job?.errorMessage }}</p-message> }
        @if (rows().length) {
          <p-table [value]="rows()">
            <ng-template #header><tr><th scope="col">Proyecto</th><th scope="col">Estado</th><th scope="col">Motivo</th><th scope="col">Descargas</th></tr></ng-template>
            <ng-template #body let-r><tr>
              <td><strong>{{ r.name }}</strong></td><td><mf-status [value]="r.status" /></td>
              <td><small>{{ r.reason }}@if (r.dependency) { ({{ r.dependency }}) }</small></td>
              <td>@for (a of r.artifacts; track a[0]) { <button type="button" class="link" (click)="api.download(a[1], r.name + '-' + a[0])"><i class="pi pi-download"></i> {{ a[0] }}</button> }</td>
            </tr></ng-template>
          </p-table>
          <ul class="artifacts">@for (a of wsArtifacts(m); track a[0]) {
            <li><button type="button" class="link" (click)="api.download(a[1], a[0])"><i class="pi pi-download"></i> {{ a[0] }}</button></li> }</ul>
        }
      }
    </mf-state>`,
  styles: [`.form{display:flex;gap:1rem;flex-wrap:wrap;margin:.5rem 0}.form span{display:flex;flex-direction:column;gap:.25rem}
    textarea{width:100%;margin:.25rem 0 .5rem}.waves{display:flex;gap:1rem;flex-wrap:wrap;margin:.5rem 0}
    .wave{border:1px solid var(--p-content-border-color,#ddd);border-radius:6px;padding:.5rem .75rem;min-width:14rem}.wave ul{margin:.25rem 0;padding-left:1rem;list-style:none}
    .ok{color:#2e7d32}.warn{color:#e65100}.bad{color:#b71c1c}.blk{color:#6d4c41}.retry{display:flex;gap:.5rem;flex-wrap:wrap;margin:.5rem 0}`],
})
export class WorkspaceMigrationPanel {
  scanId = input.required<string>();
  api = inject(Api);
  ctx = inject(ProjectContext);
  profiles = new Loader<Profile[]>();
  list = new Loader<WorkspaceMigration[]>();
  profileId: string | null = null;
  java = '21';
  acceptText = '';
  busy = signal(false);
  error = signal<UiError | null>(null);
  current = computed(() => this.list.data()?.[0] ?? null);
  rows = computed(() => Object.entries((this.current()?.results ?? {}) as Record<string, any>).map(([name, r]) => ({
    name, status: r.status, reason: r.reason, dependency: (r.blockedBy ?? []).join(', ') || r.dependency, artifacts: Object.entries(r.artifacts ?? {}) as [string, string][],
  })));
  reason = computed(() => {
    if (!this.ctx.can('execute') || !this.ctx.can('plan')) return `Su rol (${this.ctx.role()}) no permite migrar el workspace (requiere arquitecto u owner)`;
    if (['queued', 'running'].includes(this.current()?.state ?? '')) return 'Ya hay una migración de workspace en curso';
    return null;
  });

  constructor() {
    this.profiles.load(async () => {
      const ps = (await this.api.profiles()).filter(p => p.status === 'validated');
      this.profileId ??= ps.find(p => p.runtime === 'spring')?.id ?? ps[0]?.id ?? null;
      return ps;
    });
    effect(() => { const id = this.scanId(); this.list.load(() => this.api.workspaceMigrations(id)); });
  }

  migrationWaves(m: WorkspaceMigration): { wave: string; projects: string[]; status: string }[] {
    return ((m.summary as Record<string, any>)?.['migrationWaves'] ?? []);
  }
  r(m: WorkspaceMigration, n: string): any { return ((m.results ?? {}) as Record<string, any>)[n] ?? {}; }
  glyph(s?: string) { return s === 'SUCCEEDED' ? '✓' : s === 'PARTIAL_SUCCESS' ? '⚠' : s === 'BLOCKED' ? '⊘' : s ? '✗' : '·'; }
  cls(s?: string) { return s === 'SUCCEEDED' ? 'ok' : s === 'PARTIAL_SUCCESS' ? 'warn' : s === 'BLOCKED' ? 'blk' : 'bad'; }
  retryable(m: WorkspaceMigration): string[] {
    return Object.entries((m.results ?? {}) as Record<string, any>).filter(([, r]) => r.status === 'FAILED' || r.status === 'BLOCKED').map(([n]) => n);
  }
  async retry(m: WorkspaceMigration, projects: string[]) {
    this.error.set(null);
    try { await this.api.retryWorkspaceMigration(m.id, projects); this.list.reload(); }
    catch (e) { this.error.set(UiError.from(e)); }
  }

  wsArtifacts(m: WorkspaceMigration): [string, string][] {
    return Object.entries(((m.summary as Record<string, any>)?.['artifacts'] ?? {}) as Record<string, string>).map(([k, v]) => [k.replace(/^ws-[0-9a-f]+\./, ''), v]);
  }

  async start() {
    this.error.set(null);
    if (!this.profileId) { this.error.set(new UiError(422, 'VALIDATION_ERROR', 'Seleccione un perfil validado')); return; }
    const accept: Record<string, string> = {};
    for (const line of this.acceptText.split('\n').map(l => l.trim()).filter(Boolean)) {
      const i = line.indexOf('=');
      if (i < 2) { this.error.set(new UiError(422, 'VALIDATION_ERROR', `Línea inválida: ${line} (formato REGLA=motivo)`)); return; }
      accept[line.slice(0, i).trim().toUpperCase()] = line.slice(i + 1).trim();
    }
    this.busy.set(true);
    try { await this.api.startWorkspaceMigration(this.scanId(), { profileId: this.profileId, java: this.java, accept }); this.list.reload(); }
    catch (e) { this.error.set(UiError.from(e)); }
    finally { this.busy.set(false); }
  }
}

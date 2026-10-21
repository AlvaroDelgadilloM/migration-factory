import { Component, computed, effect, inject, input, signal, untracked } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MessageModule } from 'primeng/message';
import { SelectModule } from 'primeng/select';
import { TableModule } from 'primeng/table';
import { TextareaModule } from 'primeng/textarea';
import { Api } from '../../core/api';
import { UiError } from '../../core/errors';
import { Profile, TargetVersions, WorkspaceMigration } from '../../core/models';
import { ProjectContext } from '../../core/project-context';
import { Loader } from '../../shared/state';
import { ActionButton, JobLog, StateView, StatusTag } from '../../shared/ui';
import { VersionPicker, profileFor } from '../../shared/version-picker';

/** Doc 18 on the platform: migrate every project of the workspace by waves (dependencies first). */
@Component({
  selector: 'mf-ws-migration',
  imports: [FormsModule, MessageModule, SelectModule, TableModule, TextareaModule, ActionButton, JobLog, StateView, StatusTag, VersionPicker],
  template: `
    <h3>Migración por oleadas</h3>
    <p class="muted">Spring Boot / Quarkus: migra todos los proyectos del workspace en orden de dependencias. Si un proyecto no migra, los que dependen de él
      quedan <code>BLOCKED</code> (DEPENDENCY_MIGRATION_FAILED); el resto continúa. Cada proyecto se construye y prueba contra la versión
      legacy (línea base) o migrada (candidato) de sus dependencias internas.</p>
    @if (recommended(); as rec) {
      <p-message severity="info"><span><strong>Recomendado: Tomcat WAR.</strong> {{ rec.reason }}</span></p-message>
    }
    <div class="form">
      <span><label for="wsp">Destino</label>
        <p-select inputId="wsp" [options]="targets()" optionLabel="name" optionValue="id" [(ngModel)]="profileId" [style]="{ minWidth: '22rem' }" /></span>
      <mf-version-picker [entry]="versionEntry()" [(java)]="java" [(spring)]="spring" />
    </div>
    @if (profileId === TOMCAT) {
      <p class="muted">Tomcat WAR convierte todos los bundles OSGi/Blueprint del workspace en <strong>un solo WAR</strong> con Camel 4.18: las rutas no
        se rediseñan; Blueprint pasa a Spring XML y una capa de compatibilidad cubre direct-vm, xmljson y servicios OSGi. Compilar prueba los
        POM y la API; la estructura de las rutas se compara sola contra el original (G4) y las respuestas con <code>migrate.py verify responses</code>.</p>
      <label for="wsc">Configuración del cliente (JSON opcional: groupId, version, containers, patches, extraWarDependencies)</label>
      <textarea pTextarea id="wsc" rows="3" [(ngModel)]="configText" placeholder='{"groupId": "com.cliente.migrated", "version": "1.0.0"}'></textarea>
      <label class="check"><input type="checkbox" [(ngModel)]="verifyRoutes" /> Comparar la estructura de las rutas con el original (G4, tarda más)</label>
    }
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
        @if (m.target === 'tomcat-war' && tw(m).build; as b) {
          <p>Tomcat WAR · {{ tw(m).counts?.modules }} módulos · build <mf-status [value]="b.status" />
            · {{ (tw(m).manualActions ?? []).length }} punto(s) de revisión manual</p>
          @if ((tw(m).manualActions ?? []).length) { <ul>@for (a of tw(m).manualActions; track a) { <li><small>{{ a }}</small></li> }</ul> }
          @if (tw(m).equivalence; as eq) {
            <p>Equivalencia (G4): <mf-status [value]="eq.g4" /> · estructura de rutas <mf-status [value]="eq.routeStructure?.status" />
              @if (eq.routeStructure?.totals; as t) { ({{ t.identical }} idénticas, {{ t.unexpected }} distintas, {{ t.expectedByPatch }} por parche) }
              @if (eq.routeStructure?.detail) { <small class="muted">{{ eq.routeStructure.detail }}</small> }
              · respuestas <mf-status [value]="eq.responses?.status ?? 'NOT_RUN'" />
              @if (eq.responses?.totals; as rt) { ({{ rt.same }} idénticas de {{ rt.cases }} casos
                @if (eq.responses.mode === 'HUMO') { · prueba de humo, sin datos del cliente } · {{ eq.responses.at?.slice(0, 10) }}) }
              @if (!eq.responses?.totals) { <small class="muted">— se llena solo al correr Pruebas → Automáticas sobre esta migración</small> }</p>
          }
          @if (tw(m).configKeys; as ck) {
            <p>Claves de configuración: {{ ck.used }} usadas · {{ ck.definedInRepository }} definidas en el repositorio ·
              <strong>{{ ck.missing.length }}</strong> deben venir de <code>etc</code> del Tomcat destino</p>
            @if (ck.missing.length) { <ul>@for (k of ck.missing.slice(0, 20); track k.key) { <li><code>{{ k.key }}</code> <small class="muted">{{ k.usedAt[0] }}</small></li> }</ul> }
          }
          @if (tw(m).note) { <p class="muted">{{ tw(m).note }}</p> }
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
    textarea{width:100%;margin:.25rem 0 .5rem}.check{display:flex;gap:.5rem;align-items:center;margin:.25rem 0 .5rem}.waves{display:flex;gap:1rem;flex-wrap:wrap;margin:.5rem 0}
    .wave{border:1px solid var(--p-content-border-color,#ddd);border-radius:6px;padding:.5rem .75rem;min-width:14rem}.wave ul{margin:.25rem 0;padding-left:1rem;list-style:none}
    .ok{color:#2e7d32}.warn{color:#e65100}.bad{color:#b71c1c}.blk{color:#6d4c41}.retry{display:flex;gap:.5rem;flex-wrap:wrap;margin:.5rem 0}`],
})
export class WorkspaceMigrationPanel {
  scanId = input.required<string>();
  /** shared.recommendedTarget of the workspace scan: a Fuse/Karaf workspace is the tomcat-war profile's case. */
  recommended = input<{ target: string; reason: string } | null>(null);
  readonly TOMCAT = 'tomcat-war';
  targets = computed(() => [...(this.profiles.data() ?? []).map(p => ({ id: p.id, name: p.name })),
    { id: this.TOMCAT, name: 'Tomcat WAR · Fuse/Karaf (Blueprint) → Camel 4' }]);
  api = inject(Api);
  ctx = inject(ProjectContext);
  profiles = new Loader<Profile[]>();
  list = new Loader<WorkspaceMigration[]>();
  private profileSig = signal<string | null>(null);
  get profileId() { return this.profileSig(); }
  set profileId(v: string | null) { this.profileSig.set(v); }
  java = signal('21');
  spring = signal<string | null>(null);
  versions = new Loader<TargetVersions[]>();
  /** Java/Spring the chosen target accepts (Tomcat WAR or the chosen Spring Boot / Quarkus profile). */
  versionEntry = computed(() => (this.versions.data() ?? []).find(v =>
    this.profileSig() === this.TOMCAT ? v.target === 'tomcat-war' : v.profileId === this.profileSig()) ?? null);
  acceptText = '';
  configText = '';
  verifyRoutes = true;
  busy = signal(false);
  error = signal<UiError | null>(null);
  current = computed(() => this.list.data()?.[0] ?? null);
  rows = computed(() => Object.entries((this.current()?.results ?? {}) as Record<string, any>).map(([name, r]) => ({
    name, status: r.status, reason: r.reason + ((r.routeDifferences ?? []).length ? ': ' + r.routeDifferences.join(', ') : ''), dependency: (r.blockedBy ?? []).join(', ') || r.dependency, artifacts: Object.entries(r.artifacts ?? {}) as [string, string][],
  })));
  reason = computed(() => {
    if (!this.ctx.can('execute') || !this.ctx.can('plan')) return `Su rol (${this.ctx.role()}) no permite migrar el workspace (requiere arquitecto u owner)`;
    if (['queued', 'running'].includes(this.current()?.state ?? '')) return 'Ya hay una migración de workspace en curso';
    return null;
  });

  constructor() {
    this.versions.load(() => this.api.targetVersions());
    this.profiles.load(async () => (await this.api.profiles()).filter(p => p.status === 'validated'));
    // the destination chosen at the top of Inventario is where this panel starts (and follows when it changes there)
    effect(() => {
      const d = this.ctx.destination(), ps = this.profiles.data();
      if (!ps) return;
      untracked(() => {
        this.profileId = d.runtime === 'tomcat' ? this.TOMCAT
          : profileFor(ps, d) ?? ps.find(p => p.runtime === d.runtime)?.id ?? null;
        this.java.set(d.java);
        this.spring.set(d.spring);
      });
    });
    effect(() => { const id = this.scanId(); this.list.load(() => this.api.workspaceMigrations(id)); });
  }

  migrationWaves(m: WorkspaceMigration): { wave: string; projects: string[]; status: string }[] {
    return ((m.summary as Record<string, any>)?.['migrationWaves'] ?? []);
  }
  tw(m: WorkspaceMigration): any { return m.summary ?? {}; }
  r(m: WorkspaceMigration, n: string): any { return ((m.results ?? {}) as Record<string, any>)[n] ?? {}; }
  glyph(s?: string) { return s === 'SUCCEEDED' ? '✓' : s === 'PARTIAL_SUCCESS' ? '⚠' : s === 'BLOCKED' ? '⊘' : s ? '✗' : '·'; }
  cls(s?: string) { return s === 'SUCCEEDED' ? 'ok' : s === 'PARTIAL_SUCCESS' ? 'warn' : s === 'BLOCKED' ? 'blk' : 'bad'; }
  retryable(m: WorkspaceMigration): string[] {
    if (m.target === this.TOMCAT) return [];  // one WAR for the whole workspace: start a new migration instead
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
    let config: Record<string, unknown> | undefined;
    if (this.profileId === this.TOMCAT && this.configText.trim()) {
      try { config = JSON.parse(this.configText); }
      catch { this.error.set(new UiError(422, 'VALIDATION_ERROR', 'La configuración del cliente no es JSON válido')); return; }
    }
    const versions = { java: this.java(), spring: this.spring() };
    const body = this.profileId === this.TOMCAT ? { target: this.TOMCAT, ...versions, accept, config, verifyRoutes: this.verifyRoutes }
      : { target: 'profile', profileId: this.profileId, ...versions, accept };
    try { await this.api.startWorkspaceMigration(this.scanId(), body); this.list.reload(); }
    catch (e) { this.error.set(UiError.from(e)); }
    finally { this.busy.set(false); }
  }
}

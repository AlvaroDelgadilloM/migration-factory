import { Component, computed, effect, inject, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { InputTextModule } from 'primeng/inputtext';
import { MessageModule } from 'primeng/message';
import { SelectModule } from 'primeng/select';
import { TableModule } from 'primeng/table';
import { TabsModule } from 'primeng/tabs';
import { Api } from '../../core/api';
import { UiError } from '../../core/errors';
import { Artifact, Module, Page, Route, Scan } from '../../core/models';
import { ProjectContext } from '../../core/project-context';
import { CursorPager, Loader } from '../../shared/state';
import { ActionButton, JobLog, PageHeader, StateView, StatusTag, short } from '../../shared/ui';
import { WorkspacePanel } from './workspace';
import { WorkspaceMigrationPanel } from './ws-migration';

@Component({
  selector: 'mf-inventory',
  imports: [FormsModule, InputTextModule, MessageModule, SelectModule, TableModule, TabsModule, ActionButton, JobLog, PageHeader, StateView, StatusTag, WorkspacePanel, WorkspaceMigrationPanel],
  template: `
    <mf-page-header title="Inventario" crumb="INVENTARIO" [subtitle]="ctx.project()?.name">
      @if (ctx.project()?.sourceType === 'git') {
        <label class="sr-only" for="ref">Rama, tag o SHA</label>
        <input pInputText id="ref" class="ref" [(ngModel)]="ref" placeholder="rama / tag / SHA" />
      } @else if (ctx.project()) {
        <small class="muted">Origen {{ ctx.project()!.sourceType === 'zip' ? 'ZIP' : 'carpeta local' }}: se analiza un snapshot nuevo</small>
      }
      <p-select [options]="targets" optionLabel="label" optionValue="value" [(ngModel)]="target" ariaLabel="Runtime objetivo" />
      <mf-action label="Analizar" icon="pi pi-play" [busy]="starting()" [disabledReason]="scanBlockReason()" (run)="startScan()" />
    </mf-page-header>
    @if (startError(); as e) { <p-message severity="error">{{ e.code }}: {{ e.message }}</p-message> }

    <mf-state [loading]="scans.loading()" [error]="scans.error()" [hasData]="!!scans.data()" [empty]="scans.data()?.items?.length === 0"
              emptyText="Este proyecto aún no tiene análisis. Use «Analizar» para fijar un commit e inventariarlo." (retry)="scans.reload()">
      <div class="toolbar">
        <label for="scanSel">Análisis</label>
        <p-select inputId="scanSel" [options]="scans.data()?.items ?? []" [ngModel]="scanId()" (ngModelChange)="scanId.set($event)" optionValue="id"
                  [optionLabel]="'label'" [style]="{ minWidth: '28rem' }">
          <ng-template #selectedItem let-s>{{ scanLabel(s) }}</ng-template>
          <ng-template #item let-s>{{ scanLabel(s) }}</ng-template>
        </p-select>
      </div>

      @if (scan.data(); as s) {
        @if (s.status === 'failed' || s.status === 'timed_out' || s.status === 'cancelled') {
          <p-message severity="warn">Este análisis terminó en estado «{{ s.status }}»: {{ s.job?.errorMessage }}.
            @if (lastGood()) { El último análisis correcto sigue disponible en el selector. }</p-message>
        }
        @if (s.status === 'queued' || s.status === 'running') {
          <h2>Análisis en curso</h2>
          <mf-job-log [jobId]="s.jobId!" (finished)="onScanFinished()" />
          <mf-action label="Cancelar análisis" icon="pi pi-stop" severity="danger" [outlined]="true"
                     [disabledReason]="ctx.can('scan') ? null : 'Su rol no permite cancelar análisis'" (run)="cancel(s)" />
        } @else {
          <div class="cards">
            <article><b>{{ s.summary['modules'] ?? 0 }}</b> módulos Maven</article>
            <article><b>{{ s.summary['routesXmlParsed'] ?? 0 }}</b> rutas XML (parseadas)</article>
            <article><b>~{{ s.summary['routesJavaApproximate'] ?? 0 }}</b> rutas Java (aproximadas, regex)</article>
            <article><b>{{ s.summary['dynamicEndpoints'] ?? 0 }}</b> endpoints dinámicos (no resueltos)</article>
            <article><b>{{ s.summary['findings'] ?? 0 }}</b> hallazgos · {{ s.summary['errors'] ?? 0 }} errores de análisis</article>
          </div>
          @if ($any(s.summary['workspace'])?.repositoryType === 'MULTI_PROJECT_REPOSITORY' && !$any(s.summary['workspace'])?.selectedProject) {
            <mf-workspace [ws]="$any(s.summary['workspace'])" [disabledReason]="scanBlockReason()" (analyze)="startScan($event)" />
            @if (s.status === 'succeeded') { <mf-ws-migration [scanId]="s.id" /> }
          }
          <p class="meta">Snapshot <code>{{ s.snapshotHash?.slice(0, 16) }}…</code>
            @if (s.commitSha) { · commit <code>{{ s.commitSha }}</code> · ref {{ s.ref }} } · raíz Maven <code>{{ s.projectRoot }}</code>
            @if ($any(s.summary['workspace'])?.repositoryType) { ({{ $any(s.summary['workspace']).repositoryType }}) } · objetivo {{ s.target }} ·
            Resolución Maven: <mf-status [value]="s.mavenResolution === 'maven-effective' ? 'maven-effective' : s.mavenResolution" />
            @if (s.mavenResolution !== 'maven-effective') { <span class="muted"> — valores marcados como observados/no resueltos</span> }
          </p>
          <p-tabs value="modules">
            <p-tablist><p-tab value="modules">Módulos</p-tab><p-tab value="routes">Rutas</p-tab><p-tab value="errors">Errores ({{ s.errors.length }})</p-tab><p-tab value="artifacts">Reportes</p-tab></p-tablist>
            <p-tabpanels>
              <p-tabpanel value="modules">
                <mf-state [loading]="modules.loading()" [error]="modules.error()" [hasData]="!!modules.data()" [empty]="modules.data()?.length === 0"
                          emptyText="No se encontraron pom.xml" (retry)="modules.reload()">
                  <p-table [value]="modules.data() ?? []" dataKey="id" [expandedRowKeys]="expanded">
                    <ng-template #header><tr><th></th><th scope="col">POM</th><th scope="col">Artefacto</th><th scope="col">Empaquetado</th>
                      <th scope="col">Java observado</th><th scope="col">Java resuelto</th><th scope="col">Camel</th><th scope="col">Resolución</th></tr></ng-template>
                    <ng-template #body let-m let-exp="expanded">
                      <tr>
                        <td><button type="button" class="link" [pRowToggler]="m" [attr.aria-label]="'Dependencias de ' + m.artifactId">
                          <i [class]="exp ? 'pi pi-chevron-down' : 'pi pi-chevron-right'"></i></button></td>
                        <td class="mono small">{{ m.path }}</td><td>{{ m.groupId }}:{{ m.artifactId }}:{{ m.version }}</td><td>{{ m.packaging }}</td>
                        <td>{{ m.javaObserved ?? '—' }}<br /><small class="muted">{{ m.javaSource }}</small></td>
                        <td>{{ m.javaResolved ?? 'no resuelto' }}</td>
                        <td>{{ m.camelVersions.join(', ') || '—' }}</td>
                        <td><mf-status [value]="m.resolution" />
                          @for (u of m.unresolved; track u) { <br /><small class="muted">{{ u }}</small> }</td>
                      </tr>
                    </ng-template>
                    <ng-template #expandedrow let-m>
                      <tr><td colspan="8">
                        <table class="inner"><caption class="sr-only">Dependencias</caption>
                          <tr><th scope="col">Dependencia</th><th scope="col">Versión observada</th><th scope="col">Versión resuelta</th><th scope="col">Fuente</th><th scope="col">Scope</th><th scope="col">Línea</th></tr>
                          @for (d of m.dependencies; track d.groupId + d.artifactId) {
                            <tr><td class="mono small">{{ d.groupId }}:{{ d.artifactId }}</td><td>{{ d.versionObserved ?? '(heredada)' }}</td>
                              <td>{{ d.versionResolved ?? 'no resuelta' }}</td><td>{{ d.versionSource }}</td><td>{{ d.scope }}</td><td>{{ d.line }}</td></tr>
                          }
                        </table>
                      </td></tr>
                    </ng-template>
                  </p-table>
                </mf-state>
              </p-tabpanel>
              <p-tabpanel value="routes">
                <div class="toolbar">
                  <label for="dsl">DSL</label>
                  <p-select inputId="dsl" [options]="dsls" optionLabel="label" optionValue="value" [ngModel]="dsl()" (ngModelChange)="dsl.set($event); routesPager.reset(); loadRoutes()" />
                  <small class="muted">Java DSL detectado por expresión regular: puede omitir rutas dinámicas o RouteTemplates.</small>
                </div>
                <mf-state [loading]="routes.loading()" [error]="routes.error()" [hasData]="!!routes.data()" [empty]="routes.data()?.items?.length === 0"
                          emptyText="Sin rutas detectadas" (retry)="routes.reload()">
                  <p-table [value]="routes.data()?.items ?? []">
                    <ng-template #header><tr><th scope="col">Archivo:línea</th><th scope="col">routeId</th><th scope="col">DSL</th><th scope="col">Detección</th><th scope="col">from</th><th scope="col">Endpoints</th></tr></ng-template>
                    <ng-template #body let-r>
                      <tr><td class="mono small">{{ r.file }}:{{ r.line }}</td><td>{{ r.routeId ?? '—' }}</td><td>{{ r.dsl }}</td>
                        <td><small>{{ r.detection }}</small></td>
                        <td class="mono small">{{ r.dynamicFrom ? '(dinámico: desconocido)' : r.fromUri }}</td>
                        <td>@for (e of r.endpoints; track $index) { <span class="chip" [class.dyn]="e.dynamic">{{ e.component ?? '?' }}{{ e.dynamic ? ' (dinámico)' : '' }}</span> }</td></tr>
                    </ng-template>
                  </p-table>
                  <div class="pager">
                    <button type="button" class="link" [disabled]="routesPager.page() === 0" (click)="routesPager.prev(); loadRoutes()">← Anterior</button>
                    <span>{{ routes.data()?.total }} rutas · página {{ routesPager.page() + 1 }}</span>
                    <button type="button" class="link" [disabled]="!routes.data()?.nextCursor" (click)="routesPager.next(routes.data()?.nextCursor); loadRoutes()">Siguiente →</button>
                  </div>
                </mf-state>
              </p-tabpanel>
              <p-tabpanel value="errors">
                @if (s.errors.length === 0) { <div class="empty">Sin errores de análisis</div> }
                @else {
                  <p-table [value]="s.errors"><ng-template #header><tr><th scope="col">Archivo</th><th scope="col">Código</th></tr></ng-template>
                    <ng-template #body let-e><tr><td class="mono small">{{ e.file }}</td><td>{{ e.code }}</td></tr></ng-template></p-table>
                }
                @if (s.mavenResolutionDetail && s.mavenResolution === 'failed') { <h3>Detalle de Maven (redactado)</h3><pre class="log">{{ s.mavenResolutionDetail }}</pre> }
              </p-tabpanel>
              <p-tabpanel value="artifacts">
                <mf-state [loading]="artifacts.loading()" [error]="artifacts.error()" [hasData]="!!artifacts.data()" [empty]="artifacts.data()?.items?.length === 0"
                          emptyText="Sin artefactos" (retry)="artifacts.reload()">
                  <ul class="artifacts">@for (a of artifacts.data()?.items ?? []; track a.id) {
                    <li><button type="button" class="link" (click)="download(a)"><i class="pi pi-download"></i> {{ a.name }}</button>
                      <small class="muted mono">sha256 {{ a.sha256.slice(0, 16) }}… · {{ a.sizeBytes }} B</small></li> }</ul>
                </mf-state>
              </p-tabpanel>
            </p-tabpanels>
          </p-tabs>
        }
      }
    </mf-state>`,
})
export class Inventory {
  projectId = input.required<string>();
  ctx = inject(ProjectContext);
  private api = inject(Api);
  scans = new Loader<Page<Scan>>();
  scan = new Loader<Scan>();
  modules = new Loader<Module[]>();
  routes = new Loader<Page<Route>>();
  artifacts = new Loader<Page<Artifact>>();
  routesPager = new CursorPager();
  scanId = signal<string | null>(null);
  dsl = signal<string | null>(null);
  ref = signal('');
  target = signal<'spring' | 'quarkus'>('spring');
  starting = signal(false);
  startError = signal<UiError | null>(null);
  expanded: Record<string, boolean> = {};
  targets = [{ label: 'Spring Boot', value: 'spring' }, { label: 'Quarkus', value: 'quarkus' }];
  dsls = [{ label: 'Todas', value: null }, { label: 'Java (aprox.)', value: 'java' }, { label: 'XML', value: 'xml' }];
  lastGood = computed(() => this.scans.data()?.items.find(s => s.status === 'succeeded') ?? null);
  scanBlockReason = computed(() => !this.ctx.perms() ? 'Cargando permisos…' : this.ctx.can('scan') ? null : `Su rol (${this.ctx.role()}) no permite analizar`);

  constructor() {
    effect(() => { const id = this.projectId(); this.ctx.select(id); this.loadScans(true); });
    effect(() => { const p = this.ctx.project(); if (p?.targetRuntime) this.target.set(p.targetRuntime as 'spring' | 'quarkus'); });
    effect(() => { const id = this.scanId(); if (id) this.loadScan(id); });
  }

  scanLabel(s: Scan) { return `${new Date(s.createdAt).toLocaleString()} · ${s.status} · ${s.commitSha ? short(s.commitSha) : 'snap ' + short(s.snapshotHash)}`; }

  async loadScans(selectDefault = false) {
    const page = await this.scans.load(() => this.api.scans(this.projectId(), { limit: 50 }));
    if (page && (selectDefault || !this.scanId())) {
      const latest = page.items[0];
      const pick = latest && ['queued', 'running', 'succeeded'].includes(latest.status) ? latest : (page.items.find(s => s.status === 'succeeded') ?? latest);
      this.scanId.set(pick?.id ?? null);
    }
  }

  async loadScan(id: string) {
    const s = await this.scan.load(() => this.api.scan(id));
    if (s && s.status === 'succeeded') {
      this.modules.load(() => this.api.modules(id));
      this.routesPager.reset();
      this.loadRoutes();
      this.artifacts.load(() => this.api.artifacts(this.projectId(), { scanId: id }));
    }
  }

  loadRoutes() {
    const id = this.scanId();
    if (id) this.routes.load(() => this.api.routes(id, { dsl: this.dsl(), limit: 50, cursor: this.routesPager.current() }));
  }

  async startScan(projectRoot?: string) {
    this.starting.set(true);
    this.startError.set(null);
    try {
      const r = await this.api.startScan(this.projectId(), { ref: this.ref() || null, target: this.target(), projectRoot: projectRoot ?? null });
      await this.loadScans();
      this.scanId.set(r.scanId);
      this.loadScan(r.scanId);
    } catch (e) {
      this.startError.set(UiError.from(e));
    } finally {
      this.starting.set(false);
    }
  }

  async cancel(s: Scan) {
    try { await this.api.cancelJob(s.jobId!); } catch (e) { this.startError.set(UiError.from(e)); }
    this.loadScan(s.id);
  }

  onScanFinished() { this.loadScans(); const id = this.scanId(); if (id) this.loadScan(id); }
  download(a: Artifact) { this.api.download(a.id, a.name); }
}

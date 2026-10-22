import { DatePipe } from '@angular/common';
import { Component, computed, effect, inject, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ButtonModule } from 'primeng/button';
import { CheckboxModule } from 'primeng/checkbox';
import { InputTextModule } from 'primeng/inputtext';
import { SelectModule } from 'primeng/select';
import { SelectButtonModule } from 'primeng/selectbutton';
import { TableModule } from 'primeng/table';
import { TextareaModule } from 'primeng/textarea';
import { Api } from '../../core/api';
import { UiError } from '../../core/errors';
import { DataPackage, HttpCase, HttpCaseResult, HttpRunSummary, HttpTestRun, HttpTestSource } from '../../core/models';
import { ProjectContext } from '../../core/project-context';
import { Loader } from '../../shared/state';
import { ActionButton, JobLog, PageHeader, StateView, StatusTag } from '../../shared/ui';
import { CompareView, Side } from './compare';
import { PruebasDraft, Target } from './draft';

const EXAMPLE = `{
  "cases": [
    { "name": "puntos de un socio", "path": "/member/inquiry/points/95715637", "headers": { "Accept": "application/json" } },
    { "name": "SOAP consulta", "method": "POST", "path": "/cxf/profileInquiry", "contentType": "text/xml; charset=UTF-8",
      "body": "<soapenv:Envelope xmlns:soapenv=\\"http://schemas.xmlsoap.org/soap/envelope/\\"><soapenv:Body/></soapenv:Envelope>" }
  ]
}`;

@Component({
  selector: 'mf-pruebas-automaticas',
  imports: [DatePipe, FormsModule, ButtonModule, CheckboxModule, InputTextModule, SelectModule, SelectButtonModule, TableModule, TextareaModule,
            PageHeader, StateView, StatusTag, ActionButton, JobLog, CompareView],
  styles: [`
    .perf-opts{display:flex;flex-wrap:wrap;gap:10px 16px;align-items:flex-end;margin:8px 0 0 28px}.perf-opts span{display:flex;flex-direction:column;gap:4px}
    .perf-opts label{font-size:12px;color:var(--mf-muted)}.perf-opts input{width:8rem}.perf-opts small{flex-basis:100%}
    .perf{border:1px solid var(--mf-line);border-radius:10px;padding:10px 14px;margin:12px 0}.perf h4{margin:2px 0 8px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
    .perf-t{border-collapse:collapse;font-size:13.5px;font-variant-numeric:tabular-nums}.perf-t th,.perf-t td{padding:4px 14px 4px 0;text-align:left;border-bottom:1px solid var(--mf-line)}
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(16rem, 1fr)); gap: 12px 18px; }
    .field { display: flex; flex-direction: column; gap: 4px; } .field label { font-size: 12px; color: var(--mf-muted); }
    fieldset { border: 1px solid var(--mf-line); border-radius: 8px; padding: 10px 14px; margin: 0 0 14px; background: #fff; }
    legend { font-weight: 600; padding: 0 4px; } textarea { width: 100%; font: 12.5px/1.45 ui-monospace, Menlo, monospace; }
    h2 { font-size: 18px; margin: 26px 0 10px; } tr.sel td { background: #eef5f6; } .case { cursor: pointer; }
    .steps { margin: 6px 0 12px 18px; padding: 0; color: var(--mf-muted); } .steps li { margin: 2px 0; }
    details { margin: 18px 0; } summary { cursor: pointer; font-weight: 600; color: #0a5f73; }
    .pkg { padding: 8px 12px; border: 1px dashed var(--mf-line); border-radius: 8px; background: #fbfcfd; }
    pre.fmt { background: #f4f7fa; padding: 10px; border-radius: 6px; font-size: 12px; white-space: pre-wrap; }`],
  template: `
    <mf-page-header title="Pruebas automáticas" crumb="PRUEBAS / AUTOMÁTICAS"
                    subtitle="El original (JBoss, Camel 2) contra el migrado (Tomcat, Camel 4): código HTTP, Content-Type y cuerpo de cada servicio" />

    <fieldset>
      <legend>Probar una migración</legend>
      <p class="small">La plataforma hace todo:</p>
      <ol class="steps small">
        <li>levanta el código original sobre Camel 2 y el WAR migrado en Tomcat, cada uno con su base de prueba y la misma configuración de prueba;</li>
        <li>lee del código los servicios REST y SOAP que publica cada módulo y arma un caso por operación;</li>
        <li>envía cada caso a los dos, compara las respuestas, genera la evidencia y apaga los servidores.</li>
      </ol>
      @if (sources.data(); as list) {
        @if (list.length === 0) {
          <p class="muted">Este proyecto todavía no tiene migraciones Tomcat WAR. Hágala en Inventario → «Tomcat WAR».</p>
        } @else {
          <div class="grid">
            <span class="field"><label for="mg">Migración</label>
              <p-select inputId="mg" [options]="list" optionLabel="label" optionValue="workspaceMigrationId" [(ngModel)]="migrationId" /></span>
            <span class="field"><label for="ct">Contenedor de origen</label>
              <p-select inputId="ct" [options]="containerOptions()" optionLabel="label" optionValue="value" [(ngModel)]="container" /></span>
          </div>
          <div class="pkg" style="margin-top:12px">
            <b>Datos de prueba</b> <span class="muted small">(opcional)</span>
            @if (pkg(); as p) {
              <p class="small ok">{{ p.name }}: {{ p.contents.etc.length }} archivo(s) de configuración, {{ p.contents.sql.length }} script(s) SQL,
                {{ p.contents.mocks }} respuesta(s) simulada(s), {{ p.contents.values }} valor(es), {{ p.contents.cases }} caso(s) extra.
                <button class="link" (click)="pkg.set(null)">Quitar</button></p>
            } @else {
              <p class="small muted">Sin paquete se hace una <b>prueba de humo</b>: bases vacías y sistemas externos simulados que responden
                «no disponible». Así se ve si los dos lados se comportan igual, pero no se prueban casos con datos. Si el cliente entrega datos de prueba,
                cárguelos aquí antes de probar.</p>
              <label class="p-button p-button-outlined p-button-sm" for="pk"><i class="pi pi-upload" aria-hidden="true"></i>&nbsp;Cargar paquete (.zip)</label>
              <input id="pk" type="file" accept=".zip,application/zip" class="sr-only" (change)="uploadPkg($event)" />
              <button class="link small" (click)="showFormat.set(!showFormat())">Formato del paquete</button>
              @if (showFormat()) {
                <pre class="fmt">etc/&lt;ruta&gt;.properties   configuración de prueba (reemplaza a la generada con la misma ruta)
sql/*.sql               datos iniciales de las bases (H2 en modo Oracle), en orden alfabético
mocks.json              respuestas de los sistemas externos:
                        [{{ '{' }}"key": "ws.siebel.fr", "method": "POST", "contains": "texto del cuerpo",
                          "status": 200, "contentType": "text/xml", "body": "..."{{ '}' }}]
valores.json            valores de los parámetros de ruta: {{ '{' }}"memberNumber": "95715637"{{ '}' }}
casos.json              casos extra con el formato de la batería</pre>
              }
            }
            @if (pkgError(); as e) { <p class="err small">{{ e.message }}</p> }
          </div>
          <label class="inline" style="margin-top:10px"><p-checkbox [(ngModel)]="screenshots" [binary]="true" inputId="sc1" /> Capturas de pantalla en la evidencia</label>
          <label class="inline" style="margin-top:6px"><p-checkbox [(ngModel)]="perf" [binary]="true" inputId="pf1" /> Prueba de rendimiento comparativa</label>
          @if (perf) {
            <div class="perf-opts">
              <span><label for="pfu">Usuarios concurrentes</label><input pInputText id="pfu" type="number" min="1" max="100" [(ngModel)]="perfUsers" /></span>
              <span><label for="pfs">Segundos por lado</label><input pInputText id="pfs" type="number" min="10" max="900" [(ngModel)]="perfSeconds" /></span>
              <small class="muted">La misma carga contra el original y luego contra el migrado, en la misma máquina. Detecta si el migrado es más lento,
                falla con concurrencia o usa más memoria; no mide la capacidad de producción.</small>
            </div>
          }
          <div style="margin-top:12px"><mf-action label="Probar" icon="pi pi-play" [busy]="busy()" [disabledReason]="migrationReason()" (run)="startMigration()" /></div>
        }
      } @else if (sources.error(); as e) { <p class="err">{{ e.message }}</p> }
    </fieldset>

    <details>
      <summary>Contra sistemas ya levantados (por ejemplo, el JBoss de pruebas del cliente)</summary>
      <form (ngSubmit)="start()" style="margin-top:10px">
        <fieldset>
          <legend>Sistemas a comparar</legend>
          <div class="grid">
            <span class="field"><label for="lo">Original</label><input pInputText id="lo" name="lo" [(ngModel)]="targets[0].label" /></span>
            <span class="field"><label for="uo">URL base del original</label><input pInputText id="uo" name="uo" [(ngModel)]="targets[0].url" placeholder="http://jboss-qa:8181" /></span>
            <span class="field"><label for="lm">Migrado</label><input pInputText id="lm" name="lm" [(ngModel)]="targets[1].label" /></span>
            <span class="field"><label for="um">URL base del migrado</label><input pInputText id="um" name="um" [(ngModel)]="targets[1].url" placeholder="http://tomcat-qa:8080" /></span>
          </div>
        </fieldset>
        <fieldset>
          <legend>Batería de casos</legend>
          <div class="grid">
            <span class="field"><label for="nm">Nombre de la prueba</label><input pInputText id="nm" name="nm" [(ngModel)]="name" placeholder="member-inquiry" /></span>
          </div>
          <p class="muted small">El mismo formato que <code>migrate.py verify responses --cases</code>. Cada caso: name, path y, si aplica, method, headers, body, contentType
            y <code>accept</code> (motivo de una diferencia ya revisada).</p>
          <div class="actions" style="margin-bottom:8px">
            <label class="p-button p-button-outlined p-button-sm" for="file"><i class="pi pi-upload" aria-hidden="true"></i>&nbsp;Cargar archivo JSON</label>
            <input id="file" type="file" accept=".json,application/json" class="sr-only" (change)="load($event)" />
            <p-button label="Ejemplo" icon="pi pi-file" [text]="true" size="small" (onClick)="casesText = example" />
            @if (lastCases()) { <p-button label="Casos de la última prueba" icon="pi pi-history" [text]="true" size="small" (onClick)="useLast()" /> }
            @if (savedCount()) {
              <p-button [label]="'Agregar ' + savedCount() + ' guardada(s) en Manuales'" icon="pi pi-bookmark" [text]="true" size="small" (onClick)="addSaved()" />
            }
          </div>
          <textarea pTextarea name="cs" rows="12" [(ngModel)]="casesText" aria-label="Casos en JSON"></textarea>
          @if (parsed().error) { <p class="err small">{{ parsed().error }}</p> } @else { <p class="muted small">{{ parsed().cases.length }} caso(s)</p> }
          <label class="inline"><p-checkbox [(ngModel)]="screenshots" name="sc" [binary]="true" inputId="sc" /> Capturas de pantalla en la evidencia</label>
        </fieldset>
        <mf-action label="Ejecutar comparación" icon="pi pi-play" [busy]="busy()" [disabledReason]="reason()" (run)="start()" />
      </form>
    </details>
    @if (error(); as e) { <p class="err" role="alert"><strong>{{ e.code }}</strong> — {{ e.message }}</p> }

    @if (jobId(); as j) { <h2>En curso</h2><mf-job-log [jobId]="j" (finished)="finished()" /> }

    <h2>Resultados</h2>
    <mf-state [loading]="runs.loading()" [error]="runs.error()" [hasData]="!!runs.data()" [empty]="runs.data()?.length === 0"
              emptyText="Todavía no hay pruebas automáticas en este proyecto." (retry)="runs.reload()">
      <p-table [value]="runs.data() ?? []">
        <ng-template #header><tr><th scope="col">Fecha</th><th scope="col">Prueba</th><th scope="col">Tipo</th><th scope="col">Estado</th><th scope="col">Resultado</th>
          <th scope="col">Casos</th><th scope="col">Idénticos</th><th scope="col">Distintos</th><th scope="col">Sin ejecutar</th><th scope="col">Evidencia</th></tr></ng-template>
        <ng-template #body let-r>
          <tr [class.sel]="r.id === selected()?.id">
            <td><button class="link" (click)="select(r)">{{ r.createdAt | date: 'short' }}</button></td><td>{{ r.name }}</td>
            <td class="small">{{ kind(r) }}</td>
            <td><mf-status [value]="r.state" /></td><td>@if (sum(r).status; as s) { <mf-status [value]="s" /> } @else { — }</td>
            <td>{{ sum(r).totals?.cases ?? '—' }}</td><td>{{ sum(r).totals?.same ?? '—' }}</td><td>{{ sum(r).totals?.different ?? '—' }}</td>
            <td>{{ sum(r).totals?.notExecutable ?? '—' }}</td>
            <td>@if (sum(r).artifacts; as a) {
              <button class="link" (click)="api.download(a.evidence, 'evidencia-' + r.name + '.zip')"><i class="pi pi-download" aria-hidden="true"></i> HTML + capturas</button>
              <button class="link" (click)="api.download(a.responses, 'respuestas-' + r.name + '.json')">JSON</button>
              @if (a.performance) { <button class="link" (click)="api.download(a.performance, 'rendimiento-' + r.name + '.md')">Rendimiento</button> }
              @if (a.jmeterPlan) { <button class="link" (click)="api.download(a.jmeterPlan, 'plan-jmeter-' + r.name + '.jmx')">Plan JMeter</button> } } @else { — }</td>
          </tr>
        </ng-template>
      </p-table>
    </mf-state>

    @if (selected(); as r) {
      @if (sum(r); as s) {
        <h2>{{ r.name }}</h2>
        @if (s.startup; as st) {
          <p>{{ s.mode === 'DATOS' ? 'Con el paquete de datos de prueba.' : 'Prueba de humo: bases vacías y sistemas externos simulados (sin datos del cliente).' }}
            Contenedor <b>{{ s.container || 'todos' }}</b>. Arrancaron <b>{{ st.original.started }} de {{ st.original.total }}</b> módulos en el original
            y <b>{{ st.migrado.started }} de {{ st.migrado.total }}</b> en el migrado.</p>
          @if (st.original.failed.length || st.migrado.failed.length) {
            <p class="small">No arrancaron — original: {{ st.original.failed.join(', ') || 'ninguno' }} · migrado: {{ st.migrado.failed.join(', ') || 'ninguno' }}.
              <span class="muted">Si un módulo falla en los dos, es la configuración o los datos de prueba; si falla solo en uno, revisarlo.</span></p>
          }
        }
        @if (s.totals; as t) {
          <div class="cards">
            <article><b>{{ t.cases }}</b>casos</article><article><b>{{ t.same }}</b>idénticos</article>
            <article><b>{{ t.different }}</b>distintos</article><article><b>{{ t.accepted }}</b>aceptados</article>
            <article><b>{{ t.notExecutable }}</b>sin ejecutar</article>
          </div>
          @if (t.different) {
            <p>De los distintos: {{ s.differencesByKind?.['ERROR_TEXT'] ?? 0 }} devuelven el mismo código de error y cambia solo el texto;
              {{ s.differencesByKind?.['DATA'] ?? 0 }} cambian el código, el tipo o los datos.</p>
          }
          @if (s.performance; as pf) {
            <section class="perf">
              <h4>Rendimiento <mf-status [value]="pf.comparison.status" /> <span class="muted small">{{ pf.users }} usuarios · {{ pf.seconds }} s por lado · {{ pf.cases }} de {{ pf.casesTotal ?? pf.cases }} casos</span></h4>
              @if (pf.excluded) { <p class="small muted">{{ pf.excluded }} casos quedaron fuera de la medición: tardan más de {{ pf.slowMs }} ms porque esperan a un sistema externo que en esta prueba no responde (detalle en el reporte de rendimiento).</p> }
              @if (pf.original && pf.migrated) {
              <table class="perf-t">
                <thead><tr><th scope="col">Medida</th><th scope="col">Original</th><th scope="col">Migrado</th></tr></thead>
                <tbody>
                  <tr><td>Peticiones por segundo</td><td>{{ pf.original.throughput }}</td><td>{{ pf.migrated.throughput }}</td></tr>
                  <tr><td>Mediana (p50)</td><td>{{ pf.original.p50 }} ms</td><td>{{ pf.migrated.p50 }} ms</td></tr>
                  <tr><td>p95</td><td>{{ pf.original.p95 }} ms</td><td>{{ pf.migrated.p95 }} ms</td></tr>
                  <tr><td>p99</td><td>{{ pf.original.p99 }} ms</td><td>{{ pf.migrated.p99 }} ms</td></tr>
                  <tr><td>Errores (5xx o sin respuesta)</td><td>{{ pf.original.errors }}</td><td>{{ pf.migrated.errors }}</td></tr>
                  @if (pf.original.memoryMb && pf.migrated.memoryMb) {
                    <tr><td>Memoria máxima</td><td>{{ pf.original.memoryMb.max }} MB</td><td>{{ pf.migrated.memoryMb.max }} MB</td></tr>
                  }
                </tbody>
              </table>
              }
              @for (r of pf.comparison.reasons; track r) { <p class="small">· {{ r }}</p> }
              @if (!pf.comparison.reasons.length) { <p class="small muted">Sin regresiones dentro de la tolerancia ({{ pf.comparison.tolerance * 100 }} %).</p> }
            </section>
          }
          @if (s.screenshotProblem) { <p class="muted small"><i class="pi pi-info-circle" aria-hidden="true"></i> Capturas: {{ s.screenshotProblem }}</p> }
          <p-selectbutton [options]="filters" [(ngModel)]="filter" optionLabel="label" optionValue="value" aria-label="Filtrar casos" />
          <p-table [value]="visible()" styleClass="mt-2" [paginator]="visible().length > 50" [rows]="50">
            <ng-template #header><tr><th scope="col">Caso</th><th scope="col">Petición</th><th scope="col">HTTP</th><th scope="col">Resultado</th><th scope="col">Diferencia</th></tr></ng-template>
            <ng-template #body let-c>
              <tr class="case" [class.sel]="c === current()" (click)="current.set(c)">
                <td><button class="link" (click)="current.set(c)">{{ c.name }}</button></td><td><code>{{ c.method }} {{ c.path }}</code></td>
                <td>{{ c.original.status }} → {{ c.migrated.status }}</td><td><mf-status [value]="c.state" /></td>
                <td class="small">{{ c.issues.join('; ') }}@if (c.kind === 'ERROR_TEXT') { <br><span class="muted">solo el texto del error</span> }
                  @if (c.accept) { <br>Aceptada: {{ c.accept }} }</td>
              </tr>
            </ng-template>
          </p-table>
        } @else if (r.job?.errorMessage) { <p class="err">{{ r.job?.errorMessage }}</p> }
        @if (current(); as c) {
          <h2>{{ c.name }} <mf-status [value]="c.state" /></h2>
          <p><code>{{ c.method }} {{ c.path }}</code></p>
          <mf-compare [sides]="sides()" />
        }
      }
    }`,
})
export class PruebasAutomaticas {
  projectId = input.required<string>();
  ctx = inject(ProjectContext);
  api = inject(Api);
  private draft = inject(PruebasDraft);
  runs = new Loader<HttpTestRun[]>();
  sources = new Loader<(HttpTestSource & { label: string })[]>();
  get migrationId() { return this.migrationSig(); }
  set migrationId(v: string | null) { this.migrationSig.set(v); this.container = ''; }
  container = '';
  pkg = signal<DataPackage | null>(null);
  pkgError = signal<UiError | null>(null);
  showFormat = signal(false);
  targets: [Target, Target] = [{ label: '', url: '' }, { label: '', url: '' }];
  name = '';
  screenshots = true;
  perf = false;
  perfUsers = 10;
  perfSeconds = 60;
  example = EXAMPLE;
  busy = signal(false);
  error = signal<UiError | null>(null);
  jobId = signal<string | null>(null);
  selected = signal<HttpTestRun | null>(null);
  current = signal<HttpCaseResult | null>(null);
  savedCount = signal(0);
  filters = [{ label: 'Todos', value: 'all' }, { label: 'Con diferencias', value: 'diff' }];
  filterState = signal('diff');
  get filter() { return this.filterState(); }
  set filter(v: string) { this.filterState.set(v); }
  private text = signal('');
  private migrationSig = signal<string | null>(null);

  lastCases = computed(() => this.runs.data()?.find(r => r.source === 'urls')?.cases as HttpCase[] | undefined);
  parsed = computed(() => parseCases(this.text()));
  visible = computed(() => {
    const cases = this.sum(this.selected())?.cases ?? [];
    return this.filterState() === 'all' ? cases : cases.filter(c => c.state !== 'SAME');
  });
  containerOptions = computed(() => {
    const m = this.sources.data()?.find(s => s.workspaceMigrationId === this.migrationSig());
    return [{ label: 'Todos los módulos', value: '' }, ...(m?.containers ?? []).map(c => ({ label: c, value: c }))];
  });
  sides = computed<Side[]>(() => {
    const r = this.selected(), c = this.current();
    if (!r || !c) return [];
    const t = (x: Record<string, unknown> | null | undefined, fallback: string) => ({ label: String(x?.['label'] ?? fallback), url: String(x?.['url'] ?? '') });
    return [{ ...t(r.original, 'ORIGINAL (Camel 2)'), ...c.original }, { ...t(r.migrated, 'MIGRADO (Tomcat, Camel 4)'), ...c.migrated }];
  });

  constructor() {
    effect(() => {
      const id = this.projectId();
      this.targets = this.draft.targets(id);
      this.savedCount.set(this.draft.saved(id).length);
      this.reload();
      this.sources.load(async () => (await this.api.httpTestSources(id)).map(s => ({
        ...s, label: `${new Date(s.createdAt).toLocaleString()} · build ${s.build ?? '—'} · ${s.state}${s.war ? '' : ' · sin WAR'}`,
      }))).then(list => {
        const first = list?.find(s => s.war);
        if (first && !this.migrationId) this.migrationId = first.workspaceMigrationId;
      });
    });
  }

  // casesText is a plain field for ngModel; parsed() follows it through this signal
  set casesText(v: string) { this.text.set(v); }
  get casesText() { return this.text(); }

  sum(r: HttpTestRun | null): HttpRunSummary { return (r?.summary ?? {}) as HttpRunSummary; }
  kind(r: HttpTestRun) {
    if (r.source !== 'migration') return 'Sistemas levantados';
    const m = this.sum(r).mode ?? (r.dataArtifactId ? 'DATOS' : 'HUMO');
    return `Migración · ${m === 'DATOS' ? 'con datos' : 'humo'}${r.container ? ' · ' + r.container : ''}`;
  }

  private running() { return this.runs.data()?.some(r => r.state === 'queued' || r.state === 'running'); }

  migrationReason() {
    if (!this.ctx.can('execute')) return 'Su rol en el proyecto no permite ejecutar pruebas';
    const m = this.sources.data()?.find(s => s.workspaceMigrationId === this.migrationId);
    if (!m) return 'Elija una migración';
    if (!m.war) return 'Esa migración no tiene WAR compilado';
    if (this.running()) return 'Ya hay una prueba en curso';
    return null;
  }

  reason() {
    if (!this.ctx.can('execute')) return 'Su rol en el proyecto no permite ejecutar pruebas';
    if (!this.targets[0].url || !this.targets[1].url) return 'Indique la URL base de los dos sistemas';
    if (!this.name.trim()) return 'Ponga un nombre a la prueba';
    if (this.parsed().error || !this.parsed().cases.length) return 'Agregue al menos un caso válido';
    if (this.running()) return 'Ya hay una prueba en curso';
    return null;
  }

  async reload() {
    const list = await this.runs.load(() => this.api.httpRuns(this.projectId()));
    const sel = this.selected();
    const fresh = list?.find(r => r.id === sel?.id) ?? list?.find(r => !['queued', 'running'].includes(r.state)) ?? null;
    if (fresh && fresh !== sel) { this.selected.set(fresh); this.current.set(null); }
    const running = list?.find(r => r.state === 'queued' || r.state === 'running');
    if (running?.jobId && !this.jobId()) this.jobId.set(running.jobId);
  }

  select(r: HttpTestRun) { this.selected.set(r); this.current.set(null); }

  async uploadPkg(ev: Event) {
    const f = (ev.target as HTMLInputElement).files?.[0];
    if (!f) return;
    this.pkgError.set(null);
    try { this.pkg.set(await this.api.uploadDataPackage(this.projectId(), f)); } catch (e) { this.pkgError.set(UiError.from(e)); }
  }

  load(ev: Event) {
    const f = (ev.target as HTMLInputElement).files?.[0];
    if (!f) return;
    f.text().then(t => { this.casesText = t; if (!this.name) this.name = f.name.replace(/\.json$/i, ''); });
  }

  useLast() { this.casesText = JSON.stringify({ cases: this.lastCases() }, null, 2); }

  addSaved() {
    const current = this.parsed().error ? [] : this.parsed().cases;
    this.casesText = JSON.stringify({ cases: [...current, ...this.draft.saved(this.projectId())] }, null, 2);
    this.draft.clearCases(this.projectId());
    this.savedCount.set(0);
  }

  private async launch(body: unknown) {
    this.busy.set(true);
    this.error.set(null);
    try {
      const r = await this.api.startHttpRun(this.projectId(), body);
      this.jobId.set(r.jobId);
      this.selected.set(null);
      await this.reload();
    } catch (e) {
      this.error.set(UiError.from(e));
    } finally {
      this.busy.set(false);
    }
  }

  startMigration() {
    if (this.migrationReason()) return;
    return this.launch({ source: 'migration', workspaceMigrationId: this.migrationId, container: this.container || null,
                         dataPackageId: this.pkg()?.id ?? null, screenshots: this.screenshots,
                         performance: this.perf ? { users: Number(this.perfUsers) || 10, seconds: Number(this.perfSeconds) || 60 } : null });
  }

  start() {
    if (this.reason()) return;
    this.draft.saveTargets(this.projectId(), this.targets);
    return this.launch({ source: 'urls', name: this.name.trim(), original: this.targets[0], migrated: this.targets[1],
                         cases: this.parsed().cases, screenshots: this.screenshots });
  }

  finished() { this.jobId.set(null); this.selected.set(null); this.reload(); }
}

export function parseCases(text: string): { cases: HttpCase[]; error: string | null } {
  if (!text.trim()) return { cases: [], error: null };
  try {
    const data = JSON.parse(text);
    const cases = Array.isArray(data) ? data : data?.cases;
    if (!Array.isArray(cases)) return { cases: [], error: 'Se espera {"cases": [...]} o una lista de casos' };
    const bad = cases.findIndex((c: HttpCase) => !c || typeof c.name !== 'string' || typeof c.path !== 'string' || !c.path.startsWith('/'));
    if (bad >= 0) return { cases: [], error: `El caso ${bad + 1} necesita name y un path que empiece con /` };
    return { cases, error: null };
  } catch (e) {
    return { cases: [], error: `JSON inválido: ${(e as Error).message}` };
  }
}

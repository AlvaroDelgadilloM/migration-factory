import { DatePipe } from '@angular/common';
import { Component, computed, effect, inject, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ButtonModule } from 'primeng/button';
import { CheckboxModule } from 'primeng/checkbox';
import { InputTextModule } from 'primeng/inputtext';
import { SelectButtonModule } from 'primeng/selectbutton';
import { TableModule } from 'primeng/table';
import { TextareaModule } from 'primeng/textarea';
import { Api } from '../../core/api';
import { UiError } from '../../core/errors';
import { HttpCase, HttpCaseResult, HttpRunSummary, HttpTestRun } from '../../core/models';
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
  imports: [DatePipe, FormsModule, ButtonModule, CheckboxModule, InputTextModule, SelectButtonModule, TableModule, TextareaModule,
            PageHeader, StateView, StatusTag, ActionButton, JobLog, CompareView],
  styles: [`
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(16rem, 1fr)); gap: 12px 18px; }
    .field { display: flex; flex-direction: column; gap: 4px; } .field label { font-size: 12px; color: var(--mf-muted); }
    fieldset { border: 1px solid var(--mf-line); border-radius: 8px; padding: 10px 14px; margin: 0 0 14px; background: #fff; }
    legend { font-weight: 600; padding: 0 4px; } textarea { width: 100%; font: 12.5px/1.45 ui-monospace, Menlo, monospace; }
    h2 { font-size: 18px; margin: 26px 0 10px; } tr.sel td { background: #eef5f6; } .case { cursor: pointer; }`],
  template: `
    <mf-page-header title="Pruebas automáticas" crumb="PRUEBAS / AUTOMÁTICAS"
                    subtitle="La misma batería de peticiones al sistema original (JBoss) y al migrado (Tomcat): código HTTP, Content-Type y cuerpo" />
    <form (ngSubmit)="start()">
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
        <label class="inline"><p-checkbox [(ngModel)]="screenshots" name="sc" [binary]="true" inputId="sc" /> Capturas de pantalla en la evidencia (requiere Edge o Chrome en el worker)</label>
      </fieldset>
      <mf-action label="Ejecutar comparación" icon="pi pi-play" [busy]="busy()" [disabledReason]="reason()" (run)="start()" />
      @if (error(); as e) { <p class="err" role="alert"><strong>{{ e.code }}</strong> — {{ e.message }}</p> }
    </form>

    @if (jobId(); as j) { <h2>En curso</h2><mf-job-log [jobId]="j" (finished)="finished()" /> }

    <h2>Resultados</h2>
    <mf-state [loading]="runs.loading()" [error]="runs.error()" [hasData]="!!runs.data()" [empty]="runs.data()?.length === 0"
              emptyText="Todavía no hay pruebas automáticas en este proyecto." (retry)="runs.reload()">
      <p-table [value]="runs.data() ?? []">
        <ng-template #header><tr><th scope="col">Fecha</th><th scope="col">Prueba</th><th scope="col">Estado</th><th scope="col">Resultado</th>
          <th scope="col">Idénticos</th><th scope="col">Distintos</th><th scope="col">Aceptados</th><th scope="col">Sin ejecutar</th><th scope="col">Evidencia</th></tr></ng-template>
        <ng-template #body let-r>
          <tr [class.sel]="r.id === selected()?.id">
            <td><button class="link" (click)="select(r)">{{ r.createdAt | date: 'short' }}</button></td><td>{{ r.name }}</td>
            <td><mf-status [value]="r.state" /></td><td>@if (sum(r).status; as s) { <mf-status [value]="s" /> } @else { — }</td>
            <td>{{ sum(r).totals?.same ?? '—' }}</td><td>{{ sum(r).totals?.different ?? '—' }}</td>
            <td>{{ sum(r).totals?.accepted ?? '—' }}</td><td>{{ sum(r).totals?.notExecutable ?? '—' }}</td>
            <td>@if (sum(r).artifacts; as a) {
              <button class="link" (click)="api.download(a.evidence, 'evidencia-' + r.name + '.zip')"><i class="pi pi-download" aria-hidden="true"></i> HTML + capturas</button>
              <button class="link" (click)="api.download(a.responses, 'respuestas-' + r.name + '.json')">JSON</button> } @else { — }</td>
          </tr>
        </ng-template>
      </p-table>
    </mf-state>

    @if (selected(); as r) {
      @if (sum(r); as s) {
        <h2>{{ r.name }}: {{ r.original['label'] }} contra {{ r.migrated['label'] }}</h2>
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
          @if (s.screenshotProblem) { <p class="muted small"><i class="pi pi-info-circle" aria-hidden="true"></i> Capturas: {{ s.screenshotProblem }}</p> }
          <p-selectbutton [options]="filters" [(ngModel)]="filter" optionLabel="label" optionValue="value" aria-label="Filtrar casos" />
          <p-table [value]="visible()" styleClass="mt-2">
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
  targets: [Target, Target] = [{ label: '', url: '' }, { label: '', url: '' }];
  name = '';
  screenshots = true;
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

  sides = computed<Side[]>(() => {
    const r = this.selected(), c = this.current();
    if (!r || !c) return [];
    const t = (x: Record<string, unknown>) => ({ label: String(x['label'] ?? ''), url: String(x['url'] ?? '') });
    return [{ ...t(r.original), ...c.original }, { ...t(r.migrated), ...c.migrated }];
  });
  lastCases = computed(() => this.runs.data()?.[0]?.cases as HttpCase[] | undefined);
  parsed = computed(() => parseCases(this.text()));
  visible = computed(() => {
    const cases = this.sum(this.selected())?.cases ?? [];
    return this.filterState() === 'all' ? cases : cases.filter(c => c.state !== 'SAME');
  });

  constructor() {
    effect(() => {
      const id = this.projectId();
      this.targets = this.draft.targets(id);
      this.savedCount.set(this.draft.saved(id).length);
      this.reload();
    });
  }

  // casesText is a plain field for ngModel; parsed() follows it through this signal
  set casesText(v: string) { this.text.set(v); }
  get casesText() { return this.text(); }

  sum(r: HttpTestRun | null): HttpRunSummary { return (r?.summary ?? {}) as HttpRunSummary; }

  reason() {
    if (!this.ctx.can('execute')) return 'Su rol en el proyecto no permite ejecutar pruebas';
    if (!this.targets[0].url || !this.targets[1].url) return 'Indique la URL base de los dos sistemas';
    if (!this.name.trim()) return 'Ponga un nombre a la prueba';
    if (this.parsed().error || !this.parsed().cases.length) return 'Agregue al menos un caso válido';
    if (this.runs.data()?.some(r => r.state === 'queued' || r.state === 'running')) return 'Ya hay una prueba en curso';
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

  async start() {
    if (this.reason()) return;
    this.busy.set(true);
    this.error.set(null);
    this.draft.saveTargets(this.projectId(), this.targets);
    try {
      const r = await this.api.startHttpRun(this.projectId(), { name: this.name.trim(), original: this.targets[0], migrated: this.targets[1],
                                                                cases: this.parsed().cases, screenshots: this.screenshots });
      this.jobId.set(r.jobId);
      this.selected.set(null);
      await this.reload();
    } catch (e) {
      this.error.set(UiError.from(e));
    } finally {
      this.busy.set(false);
    }
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

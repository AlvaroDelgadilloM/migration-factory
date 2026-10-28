import { Component, effect, inject, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { InputTextModule } from 'primeng/inputtext';
import { SelectModule } from 'primeng/select';
import { TextareaModule } from 'primeng/textarea';
import { Api } from '../../core/api';
import { UiError } from '../../core/errors';
import { ProjectContext } from '../../core/project-context';
import { ActionButton } from '../../shared/ui';

interface RtVar { name: string; keys: string[]; required: boolean; default: string | null; secret: boolean; hint: string }
interface RtSpec { runtime: string; variables: RtVar[]; basePath: string; analysisEndpoints: string[]; allowedHosts: string[] }
const RUNTIME: Record<string, string> = { spring: 'Spring Boot (jar)', tomcat: 'Tomcat (WAR)', quarkus: 'Quarkus' };

/** Pruebas > Automáticas of a project migrated by an applied execution (Spring Boot, Tomcat WAR, Quarkus): the worker builds it,
 *  starts it with the variables given here, maps the endpoints Camel published and compares with the original test system. */
@Component({
  selector: 'mf-migrated-app-test',
  imports: [FormsModule, InputTextModule, SelectModule, TextareaModule, ActionButton],
  styles: [`
    .vars { display: grid; grid-template-columns: minmax(12rem, max-content) minmax(0, 1fr); gap: 10px 16px; align-items: start; margin: 8px 0 14px; }
    .vars .name { font: 600 12.5px ui-monospace, Menlo, monospace; word-break: break-all; } .vars small { display: block; color: var(--mf-muted); font-weight: 400; font-family: inherit; }
    .vars .in { display: flex; gap: 8px; flex-wrap: wrap; } .vars .in input { flex: 1 1 14rem; min-width: 0; }
    .req { color: #b42318; } .field { display: flex; flex-direction: column; gap: 4px; margin: 8px 0; } .field label { font-size: 12px; color: var(--mf-muted); }
    textarea { width: 100%; font: 12.5px/1.45 ui-monospace, Menlo, monospace; } .err { color: #b42318; }
    @media (max-width: 860px) { .vars { grid-template-columns: minmax(0, 1fr); } }`],
  template: `
    <fieldset>
      <legend>Probar el proyecto migrado (Spring Boot, Tomcat o Quarkus)</legend>
      <ol class="steps small">
        <li>la plataforma compila el proyecto migrado y lo levanta en su runtime con las variables que usted indica;</li>
        <li>lee del arranque de Camel los endpoints que realmente quedaron publicados y arma un caso por cada GET;</li>
        <li>si indica la URL del sistema original (ambiente de pruebas), compara respuesta por respuesta; si no, hace una prueba de humo.</li>
      </ol>
      @if (executions().length === 0) {
        <p class="muted">No hay ejecuciones aplicadas en este proyecto. Apruebe y aplique un plan (Plan → Ejecución).</p>
      } @else {
        <div class="field"><label for="rtex">Ejecución aplicada</label>
          <p-select inputId="rtex" [options]="executions()" optionLabel="label" optionValue="executionId" [(ngModel)]="executionId" /></div>
        @if (loadError(); as e) { <p class="err">{{ e.message }}</p> }
        @if (spec(); as s) {
          <p class="small">Runtime: <strong>{{ runtimeLabel(s.runtime) }}</strong> · ruta base de los servicios: <code>{{ s.basePath || '/' }}</code>.
            Hosts de prueba permitidos: <code>{{ s.allowedHosts.join(', ') }}</code>.</p>
          @if (s.variables.length) {
            <p class="small"><strong>Variables que necesita la aplicación.</strong> Los secretos se indican como referencia (<code>env:MF_CRED_…</code> o
              <code>vault:ruta#campo</code>), nunca el valor. Toda URL debe apuntar a un ambiente de pruebas permitido (nunca producción).</p>
            <div class="vars">
              @for (v of s.variables; track v.name) {
                <label class="name" [for]="'v-' + v.name">{{ v.name }} @if (v.required) { <span class="req" title="obligatoria">*</span> }
                  <small>{{ v.hint }}@if (v.default) { · por defecto <code>{{ v.default }}</code> }</small></label>
                <span class="in">
                  <input pInputText [id]="'v-' + v.name" [(ngModel)]="values[v.name]" [name]="v.name"
                         [placeholder]="v.secret ? 'env:MF_CRED_TEST_DB o vault:secret/pruebas#password' : (v.default ? 'vacío = ' + v.default : 'valor de pruebas')" />
                </span>
              }
            </div>
          } @else { <p class="small muted">La aplicación no pide variables.</p> }
          <div class="field"><label for="rtextra">Variables adicionales, una por línea <code>NOMBRE=valor</code> (Spring Boot y Quarkus las leen del entorno,
            p. ej. <code>QUARKUS_DATASOURCE_JDBC_URL=jdbc:postgresql://db-pruebas:5432/app</code>; secretos como <code>env:MF_CRED_…</code>)</label>
            <textarea pTextarea id="rtextra" rows="2" [(ngModel)]="extraText"></textarea></div>
          <div class="field"><label for="rtorig">URL base del sistema original en el ambiente de pruebas (opcional; vacío = prueba de humo)</label>
            <input pInputText id="rtorig" [(ngModel)]="original" placeholder="http://jboss-pruebas:8080/shop/api" /></div>
          <div class="field"><label for="rtparams">Valores de los parámetros de ruta, uno por línea (p. ej. <code>id=42</code>; sin valor se usa 1)</label>
            <textarea pTextarea id="rtparams" rows="2" [(ngModel)]="paramsText"></textarea></div>
          <div class="field"><label for="rtcases">Casos adicionales (POST, PUT…) en JSON, opcional: <code>[{{ '{' }}"name", "method", "path", "contentType", "body"{{ '}' }}]</code></label>
            <textarea pTextarea id="rtcases" rows="3" [(ngModel)]="casesText"></textarea>
            @if (casesError()) { <small class="err">{{ casesError() }}</small> }</div>
          @if (error(); as e) { <p class="err" role="alert"><strong>{{ e.code }}</strong> — {{ e.message }}</p> }
          <mf-action label="Levantar y probar" icon="pi pi-play" [busy]="busy()" [disabledReason]="reason()" (run)="run()" />
        }
      }
    </fieldset>`,
})
export class MigratedAppTest {
  projectId = input.required<string>();
  running = input(false);
  started = output<string>();
  private api = inject(Api);
  ctx = inject(ProjectContext);
  executions = signal<{ executionId: string; label: string; candidate: boolean }[]>([]);
  spec = signal<RtSpec | null>(null);
  loadError = signal<UiError | null>(null);
  error = signal<UiError | null>(null);
  busy = signal(false);
  values: Record<string, string> = {};
  original = '';
  paramsText = '';
  extraText = '';
  casesText = '';
  private exSig = signal<string | null>(null);
  get executionId() { return this.exSig(); }
  set executionId(v: string | null) { this.exSig.set(v); }
  casesError() { const c = this.cases(); return typeof c === 'string' ? c : ''; }

  constructor() {
    effect(() => {
      const id = this.projectId();
      this.api.runtimeTestExecutions(id)
        .then(list => {
          const usable = list.filter(e => e.candidate).map(e => ({ ...e, label: `${new Date(e.createdAt).toLocaleString()} · ${e.status ?? e.state}` }));
          this.executions.set(usable);
          if (usable.length && !this.executionId) this.executionId = usable[0].executionId;
        }).catch(e => this.loadError.set(UiError.from(e)));
    });
    effect(() => {
      const ex = this.exSig();
      this.spec.set(null);
      this.loadError.set(null);
      if (!ex) return;
      this.api.runtimeTestSpec<RtSpec>(ex).then(s => { this.spec.set(s); this.values = {}; })
        .catch(e => this.loadError.set(UiError.from(e)));
    });
  }

  runtimeLabel(r: string) { return RUNTIME[r] ?? r; }

  private cases(): unknown[] | string {
    if (!this.casesText.trim()) return [];
    try {
      const v = JSON.parse(this.casesText);
      return Array.isArray(v) ? v : 'Debe ser una lista JSON de casos';
    } catch { return 'JSON inválido'; }
  }

  reason() {
    if (!this.ctx.can('execute')) return 'Su rol en el proyecto no permite ejecutar pruebas';
    const s = this.spec();
    if (!s) return 'Elija una ejecución aplicada';
    const missing = s.variables.filter(v => v.required && !this.values[v.name]?.trim()).map(v => v.name);
    if (missing.length) return `Faltan: ${missing.join(', ')}`;
    if (typeof this.cases() === 'string') return 'Revise los casos adicionales (JSON)';
    if (this.running()) return 'Ya hay una prueba en curso';
    return null;
  }

  async run() {
    const s = this.spec();
    if (!s || this.reason()) return;
    const variables: Record<string, { value?: string; ref?: string }> = {};
    for (const v of s.variables) {
      const x = this.values[v.name]?.trim();
      if (!x) continue;
      variables[v.name] = /^(env:MF_CRED_|vault:)/.test(x) ? { ref: x } : { value: x };
    }
    for (const line of this.extraText.split('\n')) {
      const i = line.indexOf('=');
      const name = line.slice(0, i).trim(), x = line.slice(i + 1).trim();
      if (i > 0 && /^[A-Z][A-Z0-9_]{1,100}$/.test(name) && x) variables[name] = /^(env:MF_CRED_|vault:)/.test(x) ? { ref: x } : { value: x };
    }
    const params = Object.fromEntries(this.paramsText.split('\n').map(l => l.split('=').map(p => p.trim())).filter(p => p.length === 2 && p[0] && p[1]));
    const body = { source: 'execution', executionId: this.executionId, variables, params, cases: this.cases(), screenshots: true,
                   original: this.original.trim() ? { label: 'ORIGINAL (pruebas)', url: this.original.trim() } : null };
    this.busy.set(true);
    this.error.set(null);
    try {
      const r = await this.api.startHttpRun(this.projectId(), body) as { jobId: string };
      this.started.emit(r.jobId);
    } catch (e) { this.error.set(UiError.from(e)); }
    finally { this.busy.set(false); }
  }
}

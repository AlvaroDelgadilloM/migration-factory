import { Component, effect, inject, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ButtonModule } from 'primeng/button';
import { CheckboxModule } from 'primeng/checkbox';
import { InputTextModule } from 'primeng/inputtext';
import { SelectModule } from 'primeng/select';
import { TextareaModule } from 'primeng/textarea';
import { Api } from '../../core/api';
import { UiError } from '../../core/errors';
import { HttpCase, ManualResult } from '../../core/models';
import { ProjectContext } from '../../core/project-context';
import { ActionButton, PageHeader, StatusTag } from '../../shared/ui';
import { CompareView } from './compare';
import { PruebasDraft, Target } from './draft';

type Kind = 'rest' | 'xml' | 'soap' | 'otro';
const KINDS: { value: Kind; label: string }[] = [
  { value: 'rest', label: 'REST (JSON)' }, { value: 'xml', label: 'XML' }, { value: 'soap', label: 'SOAP' }, { value: 'otro', label: 'Otro' },
];
const TEMPLATES: Record<Kind, { method: string; contentType: string; body: string }> = {
  rest: { method: 'GET', contentType: 'application/json', body: '' },
  xml: { method: 'POST', contentType: 'application/xml; charset=UTF-8', body: '<?xml version="1.0" encoding="UTF-8"?>\n<request>\n</request>' },
  soap: { method: 'POST', contentType: 'text/xml; charset=UTF-8',
          body: '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">\n  <soapenv:Header/>\n  <soapenv:Body>\n  </soapenv:Body>\n</soapenv:Envelope>' },
  otro: { method: 'GET', contentType: 'text/plain', body: '' },
};

@Component({
  selector: 'mf-pruebas-manuales',
  imports: [FormsModule, ButtonModule, CheckboxModule, InputTextModule, SelectModule, TextareaModule, PageHeader, ActionButton, StatusTag, CompareView],
  styles: [`
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(18rem, 1fr)); gap: 12px 18px; }
    .field { display: flex; flex-direction: column; gap: 4px; } .field label { font-size: 12px; color: var(--mf-muted); }
    .headers .row { margin-bottom: 6px; } .headers input { flex: 1; }
    textarea { width: 100%; font: 12.5px/1.45 ui-monospace, Menlo, monospace; }
    fieldset { border: 1px solid var(--mf-line); border-radius: 8px; padding: 10px 14px; margin: 0 0 14px; background: #fff; }
    legend { font-weight: 600; padding: 0 4px; } .result-head { display: flex; gap: .75rem; align-items: center; margin: 18px 0 10px; flex-wrap: wrap; }`],
  template: `
    <mf-page-header title="Pruebas manuales" crumb="PRUEBAS / MANUALES"
                    subtitle="Una petición (REST, XML o SOAP) a un sistema, o al original y al migrado para compararlos" />
    <form (ngSubmit)="send()">
      <fieldset>
        <legend>Sistemas</legend>
        <div class="grid">
          <span class="field"><label for="la">Sistema A</label><input pInputText id="la" name="la" [(ngModel)]="targets[0].label" /></span>
          <span class="field"><label for="ua">URL base de A</label><input pInputText id="ua" name="ua" [(ngModel)]="targets[0].url" placeholder="http://jboss-qa:8181" required /></span>
        </div>
        <label class="inline" style="margin:10px 0"><p-checkbox [(ngModel)]="compare" name="cmp" [binary]="true" inputId="cmp" /> Comparar contra un segundo sistema</label>
        @if (compare) {
          <div class="grid">
            <span class="field"><label for="lb">Sistema B</label><input pInputText id="lb" name="lb" [(ngModel)]="targets[1].label" /></span>
            <span class="field"><label for="ub">URL base de B</label><input pInputText id="ub" name="ub" [(ngModel)]="targets[1].url" placeholder="http://tomcat-qa:8080" /></span>
          </div>
        }
        <p class="muted small">Solo se aceptan los servidores autorizados por el administrador de la plataforma (MF_TEST_ALLOWED_HOSTS).</p>
      </fieldset>
      <fieldset>
        <legend>Petición</legend>
        <div class="grid">
          <span class="field"><label for="tp">Tipo</label>
            <p-select inputId="tp" name="tp" [options]="kinds" optionLabel="label" optionValue="value" [(ngModel)]="kind" (onChange)="useTemplate()" /></span>
          <span class="field"><label for="mt">Método</label><p-select inputId="mt" name="mt" [options]="methods" [(ngModel)]="method" /></span>
          <span class="field"><label for="ct">Content-Type</label><input pInputText id="ct" name="ct" [(ngModel)]="contentType" /></span>
        </div>
        <span class="field" style="margin-top:10px"><label for="pt">Ruta (se agrega a la URL base)</label>
          <input pInputText id="pt" name="pt" [(ngModel)]="path" placeholder="/member/inquiry/points/95715637" required /></span>
        @if (kind === 'soap') {
          <span class="field" style="margin-top:10px"><label for="sa">SOAPAction</label><input pInputText id="sa" name="sa" [(ngModel)]="soapAction" placeholder='"urn:operacion"' /></span>
        }
        <div class="headers" style="margin-top:10px">
          <label class="muted small">Headers</label>
          @for (h of headers; track $index) {
            <div class="row">
              <input pInputText [name]="'hk' + $index" [(ngModel)]="h.k" placeholder="Nombre" aria-label="Nombre del header" />
              <input pInputText [name]="'hv' + $index" [(ngModel)]="h.v" placeholder="Valor" aria-label="Valor del header" />
              <p-button icon="pi pi-trash" [text]="true" severity="danger" (onClick)="headers.splice($index, 1)" ariaLabel="Quitar header" />
            </div>
          }
          <p-button label="Agregar header" icon="pi pi-plus" [text]="true" size="small" (onClick)="headers.push({ k: '', v: '' })" />
        </div>
        @if (method !== 'GET' && method !== 'DELETE' && method !== 'HEAD') {
          <span class="field" style="margin-top:10px"><label for="bd">Cuerpo</label>
            <textarea pTextarea id="bd" name="bd" rows="10" [(ngModel)]="body"></textarea></span>
        }
      </fieldset>
      <div class="actions">
        <mf-action label="Enviar" icon="pi pi-send" [busy]="busy()" [disabledReason]="reason()" (run)="send()" />
        <p-button label="Guardar en la batería automática" icon="pi pi-bookmark" [outlined]="true" [disabled]="!path" (onClick)="save()" />
      </div>
      @if (savedMsg()) { <p class="ok small">{{ savedMsg() }}</p> }
    </form>
    @if (error(); as e) { <p class="err" role="alert"><strong>{{ e.code }}</strong> — {{ e.message }}</p> }
    @if (result(); as r) {
      <div class="result-head">
        <h2 style="margin:0;font-size:18px">Resultado</h2>
        @if (r.state) { <mf-status [value]="r.state" /> }
        @if (r.issues?.length) { <span>{{ r.issues!.join('; ') }}</span> }
        @if (r.kind === 'ERROR_TEXT') { <span class="muted small">Mismo código de error; cambia solo el texto</span> }
      </div>
      <mf-compare [sides]="r.results" />
    }`,
})
export class PruebasManuales {
  projectId = input.required<string>();
  ctx = inject(ProjectContext);
  private api = inject(Api);
  private draft = inject(PruebasDraft);
  kinds = KINDS;
  methods = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'];
  kind: Kind = 'rest';
  method = 'GET';
  contentType = 'application/json';
  path = '';
  soapAction = '';
  body = '';
  headers: { k: string; v: string }[] = [{ k: 'Accept', v: 'application/json' }];
  targets: [Target, Target] = [{ label: '', url: '' }, { label: '', url: '' }];
  compare = true;
  busy = signal(false);
  error = signal<UiError | null>(null);
  result = signal<ManualResult | null>(null);
  savedMsg = signal('');

  constructor() {
    effect(() => { this.targets = this.draft.targets(this.projectId()); });
  }

  reason() {
    if (!this.ctx.can('execute')) return 'Su rol en el proyecto no permite ejecutar pruebas';
    if (!this.targets[0].url || (this.compare && !this.targets[1].url)) return 'Indique la URL base de cada sistema';
    if (!this.path.startsWith('/')) return 'La ruta debe empezar con /';
    return null;
  }

  useTemplate() {
    const t = TEMPLATES[this.kind];
    this.method = t.method;
    this.contentType = t.contentType;
    this.body = t.body;
    this.headers = this.kind === 'rest' ? [{ k: 'Accept', v: 'application/json' }] : [];
  }

  private request(): HttpCase {
    const headers: Record<string, string> = {};
    for (const h of this.headers) if (h.k.trim()) headers[h.k.trim()] = h.v;
    if (this.kind === 'soap' && this.soapAction) headers['SOAPAction'] = this.soapAction;
    const hasBody = !['GET', 'DELETE', 'HEAD'].includes(this.method) && this.body;
    return { name: `${this.method} ${this.path}`, method: this.method as HttpCase['method'], path: this.path, headers,
             ...(hasBody ? { body: this.body, contentType: this.contentType } : {}) } as HttpCase;
  }

  async send() {
    if (this.reason()) return;
    this.busy.set(true);
    this.error.set(null);
    this.draft.saveTargets(this.projectId(), this.targets);
    try {
      const targets = this.compare ? this.targets : [this.targets[0]];
      this.result.set(await this.api.manualTest(this.projectId(), { request: this.request(), targets }));
    } catch (e) {
      this.error.set(UiError.from(e));
    } finally {
      this.busy.set(false);
    }
  }

  save() {
    this.draft.addCase(this.projectId(), this.request());
    this.savedMsg.set(`Guardada. Hay ${this.draft.saved(this.projectId()).length} petición(es) para agregar en Pruebas automáticas.`);
  }
}

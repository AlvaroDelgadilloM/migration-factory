import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { DialogModule } from 'primeng/dialog';
import { InputTextModule } from 'primeng/inputtext';
import { MessageModule } from 'primeng/message';
import { SelectModule } from 'primeng/select';
import { TableModule } from 'primeng/table';
import { TabsModule } from 'primeng/tabs';
import { TextareaModule } from 'primeng/textarea';
import { Api } from '../../core/api';
import { AuthService } from '../../core/auth';
import { UiError } from '../../core/errors';
import { Catalog, Profile } from '../../core/models';
import { Loader } from '../../shared/state';
import { ActionButton, PageHeader, StateView, StatusTag } from '../../shared/ui';

@Component({
  selector: 'mf-rules',
  imports: [FormsModule, DialogModule, InputTextModule, MessageModule, SelectModule, TableModule, TabsModule, TextareaModule, ActionButton, PageHeader, StateView, StatusTag],
  template: `
    <mf-page-header title="Reglas" crumb="REGLAS" subtitle="Catálogo versionado de diagnóstico y perfiles objetivo">
      <mf-action label="Nueva versión de catálogo" icon="pi pi-plus" [disabledReason]="adminReason()" (run)="openNew()" />
    </mf-page-header>
    @if (error(); as e) { <p-message severity="error">{{ e.code }}: {{ e.message }}
      @if (e.details['errors']) { <ul>@for (x of asList(e.details['errors']); track x) { <li>{{ x }}</li> }</ul> } </p-message> }
    <p-tabs value="catalog">
      <p-tablist><p-tab value="catalog">Catálogos</p-tab><p-tab value="profiles">Perfiles objetivo</p-tab></p-tablist>
      <p-tabpanels>
        <p-tabpanel value="catalog">
          <mf-state [loading]="catalogs.loading()" [error]="catalogs.error()" [hasData]="!!catalogs.data()" [empty]="catalogs.data()?.length === 0" (retry)="catalogs.reload()">
            <div class="toolbar">
              <label for="cv">Versión</label>
              <p-select inputId="cv" [options]="catalogs.data() ?? []" optionValue="id" [ngModel]="selected()" (ngModelChange)="selected.set($event)">
                <ng-template #selectedItem let-c>{{ c.version }} · {{ c.status }}</ng-template><ng-template #item let-c>{{ c.version }} · {{ c.status }}</ng-template></p-select>
              @if (current(); as c) {
                <span>digest <code>{{ c.digest.slice(0, 12) }}</code> · {{ c.rules.length }} reglas · <mf-status [value]="c.status" /></span>
                <mf-action label="Activar" icon="pi pi-check" severity="success" [disabledReason]="adminReason() ?? (c.status !== 'draft' ? 'Solo se activa un borrador; el histórico no se edita' : null)" (run)="activate(c)" />
              }
            </div>
            <p class="muted">Indicios textuales/estructurales revisables, no una matriz completa de compatibilidad Camel. Cada regla trae ejemplos positivos y negativos que se prueban al crear el catálogo.</p>
            <p-table [value]="current()?.rules ?? []">
              <ng-template #header><tr><th scope="col">Regla</th><th scope="col">Detector</th><th scope="col">Severidad</th><th scope="col">Clase</th><th scope="col">Bloquea</th><th scope="col">Patrón / receta</th><th scope="col">Fuente</th><th scope="col">Ejemplos</th></tr></ng-template>
              <ng-template #body let-r><tr><td><strong>{{ r.id }}</strong> v{{ r.version }}<br /><small>{{ r.recommendation }}</small></td><td>{{ r.detector }}</td>
                <td><mf-status [value]="r.severity" /></td><td>{{ r.classification }}</td><td>{{ r.blocking ? 'Sí' : 'No' }}</td>
                <td class="mono small">{{ r.pattern ?? '—' }}@if (r.recipe) { <br />→ {{ r.recipe }} }</td>
                <td><a [href]="r.source" target="_blank" rel="noopener">fuente</a></td>
                <td>{{ r.examples?.positive?.length ?? 0 }} + / {{ r.examples?.negative?.length ?? 0 }} −</td></tr></ng-template>
            </p-table>
          </mf-state>
        </p-tabpanel>
        <p-tabpanel value="profiles">
          <mf-state [loading]="profiles.loading()" [error]="profiles.error()" [hasData]="!!profiles.data()" (retry)="profiles.reload()">
            <p-table [value]="profiles.data() ?? []">
              <ng-template #header><tr><th scope="col">Perfil</th><th scope="col">Runtime</th><th scope="col">Java</th><th scope="col">Camel</th><th scope="col">BOM</th><th scope="col">Herramientas fijadas</th><th scope="col">Estado</th><th scope="col"></th></tr></ng-template>
              <ng-template #body let-p><tr><td><strong>{{ p.name }}</strong><br /><small class="mono">{{ p.key }} v{{ p.version }} · {{ p.digest.slice(0, 10) }}</small></td>
                <td>{{ p.runtime }} {{ p.runtimeVersion }}</td><td>{{ p.javaVersion }}</td><td>{{ p.camelVersion }}</td>
                <td class="mono small">@for (b of p.bomCoordinates; track b) { <div>{{ b }}</div> }</td>
                <td class="mono small">rewrite-maven-plugin {{ p.tooling.pluginVersion }}@for (a of p.tooling.artifacts; track a) { <div>{{ a }}</div> }</td>
                <td><mf-status [value]="p.status" /></td>
                <td><mf-action label="Retirar" severity="secondary" [outlined]="true" [disabledReason]="adminReason() ?? (p.status === 'retired' ? 'Ya retirado' : null)" (run)="retire(p)" />
                  @if (p.status === 'draft') { <mf-action label="Validar" severity="success" [disabledReason]="adminReason()" (run)="validate(p)" /> }</td></tr></ng-template>
            </p-table>
            <p class="muted">Un perfil usado no se edita: se crea una versión nueva (API POST /profiles).</p>
          </mf-state>
        </p-tabpanel>
      </p-tabpanels>
    </p-tabs>

    <p-dialog header="Nueva versión de catálogo" [(visible)]="dialog" [modal]="true" [style]="{ width: '50rem' }">
      <form class="form" (ngSubmit)="create()">
        <label for="ver">Versión</label><input pInputText id="ver" name="ver" [(ngModel)]="newVersion" />
        <label for="json">Reglas (JSON: lista de reglas). Se valida regex y ejemplos positivos/negativos.</label>
        <textarea pTextarea id="json" name="json" rows="14" class="mono" [(ngModel)]="newRules"></textarea>
        <div class="dialog-actions"><mf-action label="Crear borrador" [disabledReason]="newVersion ? null : 'Indique versión'" (run)="create()" /></div>
      </form>
    </p-dialog>`,
})
export class Rules {
  private api = inject(Api);
  private auth = inject(AuthService);
  catalogs = new Loader<Catalog[]>();
  profiles = new Loader<Profile[]>();
  selected = signal<string | null>(null);
  error = signal<UiError | null>(null);
  dialog = false;
  newVersion = '';
  newRules = '';
  current = computed(() => this.catalogs.data()?.find(c => c.id === this.selected()) ?? null);
  adminReason = computed(() => this.auth.me()?.isAdmin ? null : 'Requiere administrador de plataforma');

  constructor() { this.loadAll(); }

  async loadAll() {
    const cs = await this.catalogs.load(() => this.api.catalogs());
    this.selected.set(cs?.find(c => c.status === 'active')?.id ?? cs?.[0]?.id ?? null);
    this.profiles.load(() => this.api.profiles());
  }

  asList(x: unknown) { return x as string[]; }
  openNew() { this.newVersion = ''; this.newRules = JSON.stringify(this.current()?.rules ?? [], null, 2); this.dialog = true; }

  private async act(fn: () => Promise<unknown>) {
    this.error.set(null);
    try { await fn(); await this.loadAll(); return true; } catch (e) { this.error.set(UiError.from(e)); return false; }
  }

  async create() {
    let rules: unknown;
    try { rules = JSON.parse(this.newRules); } catch { this.error.set(new UiError(422, 'JSON_INVALID', 'El JSON de reglas no es válido')); return; }
    if (await this.act(() => this.api.createCatalog({ catalogVersion: this.newVersion, rules }))) this.dialog = false;
  }
  activate(c: Catalog) { this.act(() => this.api.activateCatalog(c.id)); }
  retire(p: Profile) { this.act(() => this.api.setProfileStatus(p.id, 'retired')); }
  validate(p: Profile) { this.act(() => this.api.setProfileStatus(p.id, 'validated')); }
}

import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { ButtonModule } from 'primeng/button';
import { DialogModule } from 'primeng/dialog';
import { InputTextModule } from 'primeng/inputtext';
import { MessageModule } from 'primeng/message';
import { SelectModule } from 'primeng/select';
import { TableModule } from 'primeng/table';
import { TextareaModule } from 'primeng/textarea';
import { Api } from '../../core/api';
import { UiError } from '../../core/errors';
import { Capabilities, Page, Project, SourceType, Upload } from '../../core/models';
import { buildZip, filterPicked } from '../../core/zip';
import { FolderBrowser } from './folder-browser';
import { ProjectContext } from '../../core/project-context';
import { CursorPager, Loader } from '../../shared/state';
import { PageHeader, StateView, StatusTag, short } from '../../shared/ui';

const URL_RE = /^https:\/\/[\w.-]+(:443)?\/[\w.\-/~]+$/;

@Component({
  selector: 'mf-portfolio',
  imports: [FormsModule, RouterLink, ButtonModule, DialogModule, InputTextModule, MessageModule, SelectModule, TableModule, TextareaModule,
            PageHeader, StateView, StatusTag, FolderBrowser],
  template: `
    <mf-page-header title="Cartera" crumb="CARTERA" subtitle="Aplicaciones e integraciones visibles para su usuario">
      <p-button label="Registrar proyecto" icon="pi pi-plus" (onClick)="openNew()" />
    </mf-page-header>

    <div class="cards">
      <article><b>{{ list.data()?.total ?? '—' }}</b> proyectos visibles</article>
      <article><b>{{ analyzed() }}</b> con análisis correcto (en esta página)</article>
      <article><b>{{ withFindings() }}</b> con hallazgos (en esta página)</article>
    </div>

    <div class="toolbar">
      <span class="p-input-icon-left">
        <label class="sr-only" for="q">Buscar proyecto</label>
        <input pInputText id="q" placeholder="Buscar por nombre" [(ngModel)]="q" (keyup.enter)="search()" />
      </span>
      <p-button label="Buscar" icon="pi pi-search" [outlined]="true" (onClick)="search()" />
    </div>

    <mf-state [loading]="list.loading()" [error]="list.error()" [hasData]="!!list.data()" [empty]="list.data()?.items?.length === 0"
              emptyText="No hay proyectos visibles. Registre uno o pida acceso a un owner." (retry)="list.reload()">
      <div class="panel">
        <p-table [value]="list.data()?.items ?? []" [loading]="list.loading()" dataKey="id" [tableStyle]="{ 'min-width': '52rem' }">
          <ng-template #header>
            <tr><th scope="col">Proyecto</th><th scope="col">Origen</th><th scope="col">Runtime</th><th scope="col">Rol</th>
                <th scope="col">Último análisis</th><th scope="col">Commit / snapshot</th><th scope="col">Hallazgos</th><th scope="col"></th></tr>
          </ng-template>
          <ng-template #body let-p>
            <tr>
              <td><a [routerLink]="['/p', p.id, 'inventory']"><strong>{{ p.name }}</strong></a><br /><small class="muted">{{ p.description }}</small></td>
              <td class="small"><span class="chip">{{ sourceLabel[p.sourceType] }}</span>
                <span class="mono">{{ p.repositoryUrl ?? p.localPath ?? p.upload?.['filename'] }}</span></td>
              <td>{{ p.targetRuntime ?? '—' }}</td>
              <td>{{ p.myRole ?? '—' }}</td>
              <td>@if (p.lastScan) { <mf-status [value]="p.lastScan.status" /> } @else { <span class="muted">No ejecutado</span> }</td>
              <td class="mono">{{ p.lastScan?.commitSha ? short(p.lastScan?.commitSha) : (p.lastScan?.snapshotHash ? 'snap ' + short(p.lastScan?.snapshotHash) : '—') }}</td>
              <td>{{ p.lastScan?.findings ?? '—' }}</td>
              <td><p-button icon="pi pi-arrow-right" [text]="true" [ariaLabel]="'Abrir ' + p.name" (onClick)="open(p)" /></td>
            </tr>
          </ng-template>
        </p-table>
      </div>
      <div class="pager">
        <p-button label="Anterior" icon="pi pi-chevron-left" [text]="true" [disabled]="pager.page() === 0" (onClick)="pager.prev(); load()" />
        <span>Página {{ pager.page() + 1 }}</span>
        <p-button label="Siguiente" icon="pi pi-chevron-right" iconPos="right" [text]="true" [disabled]="!list.data()?.nextCursor"
                  (onClick)="pager.next(list.data()?.nextCursor); load()" />
      </div>
    </mf-state>

    <p-dialog header="Registrar proyecto" [(visible)]="dialog" [modal]="true" [style]="{ width: '44rem' }">
      <form class="form" (ngSubmit)="create()" #f="ngForm">
        <fieldset class="source">
          <legend>Origen del proyecto <span class="req" aria-hidden="true">*</span></legend>
          @for (o of sourceOptions(); track o.value) {
            <label class="radio" [class.disabled]="o.disabled">
              <input type="radio" name="sourceType" [value]="o.value" [(ngModel)]="form.sourceType" [disabled]="o.disabled" required />
              <span><strong>{{ o.label }}</strong><br /><small class="muted">{{ o.hint }}</small></span>
            </label>
          }
        </fieldset>

        <label for="name">Nombre</label>
        <input pInputText id="name" name="name" required minlength="2" [(ngModel)]="form.name" />
        <label for="rt">Runtime objetivo</label>
        <p-select inputId="rt" name="rt" [options]="runtimes" optionLabel="label" optionValue="value" [(ngModel)]="form.targetRuntime" />

        @switch (form.sourceType) {
          @case ('local') {
            <label for="path">Ruta del proyecto</label>
            <span class="row">
              <input pInputText id="path" name="path" required [(ngModel)]="form.localPath" [placeholder]="(caps()?.localSourceRoots?.[0] ?? '/sources') + '/mi-proyecto'" />
              <p-button label="Examinar…" icon="pi pi-folder-open" [outlined]="true" (onClick)="browsing.set(true)" />
            </span>
            <small class="muted">Ruta en el equipo donde corre el worker (no en su navegador). Directorios autorizados:
              <code>{{ caps()?.localSourceRoots?.join(', ') }}</code>. Se analiza una copia (snapshot); la carpeta nunca se modifica.</small>
            @if (form.localPath && !pathOk()) { <small class="err">Use una ruta absoluta dentro de un directorio autorizado</small> }
          }
          @case ('zip') {
            <span class="row two">
              <span><label for="zip">Archivo ZIP (máx. {{ caps()?.maxUploadMb ?? 200 }} MB)</label>
                <input id="zip" type="file" accept=".zip,application/zip" (change)="pick($event)" /></span>
              <span><label for="dir">o una carpeta de mi equipo</label>
                <input id="dir" type="file" webkitdirectory multiple (change)="pickFolder($event)" /></span>
            </span>
            <small class="muted">La carpeta se comprime en su navegador (sin .git, target ni node_modules) y se sube como ZIP; no se envía su ruta.</small>
            @if (uploading()) { <small class="muted"><i class="pi pi-spin pi-spinner"></i> {{ progress() || 'Subiendo y validando estructura…' }}</small> }
            @if (uploaded(); as u) {
              <p-message severity="success"><span>{{ u.filename }} · raíz Maven <code>{{ u.structure['root'] }}</code> ·
                módulos: {{ $any(u.structure['modules']).join(', ') }} · sha256 {{ u.sha256.slice(0, 12) }}…</span></p-message>
            }
            <small class="muted">Se rechazan rutas fuera del archivo, enlaces simbólicos, cifrado y compresión excesiva. La extracción ocurre aislada en el worker.</small>
          }
          @case ('git') {
            <label for="url">URL HTTPS del repositorio</label>
            <input pInputText id="url" name="url" required [(ngModel)]="form.repositoryUrl" placeholder="https://github.com/org/repo.git" />
            <small class="muted">Hosts permitidos: {{ caps()?.gitAllowedHosts?.join(', ') }}. Sin credenciales en la URL.</small>
            @if (form.repositoryUrl && !urlOk()) { <small class="err">Solo https://host/ruta (no file://, ssh ni http)</small> }
            <label for="branch">Rama o referencia</label>
            <input pInputText id="branch" name="branch" [(ngModel)]="form.defaultBranch" />
            <label for="cred">Referencia de credencial (opcional)</label>
            <input pInputText id="cred" name="cred" [(ngModel)]="form.credentialRef" placeholder="env:MF_CRED_GITHUB" />
            <small class="muted">Nunca el token: env:MF_CRED_* o vault:ruta (adaptador vault pendiente).</small>
          }
        }

        <label for="desc">Descripción (opcional)</label>
        <textarea pTextarea id="desc" name="desc" rows="2" [(ngModel)]="form.description"></textarea>
        @if (createError(); as e) { <p-message severity="error">{{ e.code }}: {{ e.message }}</p-message> }
        <div class="dialog-actions">
          <p-button label="Cancelar" [text]="true" (onClick)="dialog = false" />
          <p-button type="submit" label="Registrar" [disabled]="!f.valid || !sourceReady()" [loading]="creating()" />
        </div>
      </form>
    </p-dialog>
    <mf-folder-browser [visible]="browsing()" [start]="form.localPath || null" (selected)="form.localPath = $event; browsing.set(false)" (closed)="browsing.set(false)" />`,
})
export class Portfolio {
  private api = inject(Api);
  private router = inject(Router);
  private ctx = inject(ProjectContext);
  list = new Loader<Page<Project>>();
  pager = new CursorPager();
  q = signal('');
  dialog = false;
  creating = signal(false);
  createError = signal<UiError | null>(null);
  form = this.emptyForm();
  caps = signal<Capabilities | null>(null);
  uploading = signal(false);
  uploaded = signal<Upload | null>(null);
  browsing = signal(false);
  progress = signal('');
  sourceOptions = computed(() => {
    const local = this.caps()?.localSourceEnabled ?? false;
    return [
      { value: 'local', label: 'Carpeta local', disabled: !local,
        hint: local ? 'Carpeta del equipo donde corre la plataforma, dentro de un directorio autorizado.'
                    : 'No disponible: la plataforma no se ejecuta en su equipo. Use «Archivo ZIP» (acepta también una carpeta de su equipo); una ruta de su computadora requiere un agente local.' },
      { value: 'zip', label: 'Archivo ZIP', disabled: false, hint: 'Suba un ZIP o elija una carpeta de su equipo (se comprime en el navegador). No requiere Git.' },
      { value: 'git', label: 'Repositorio Git', disabled: false, hint: 'Repositorio HTTPS en un host permitido; habilita ramas y PR borrador.' },
    ];
  });
  runtimes = [{ label: 'Spring Boot', value: 'spring' }, { label: 'Quarkus', value: 'quarkus' }];
  short = short;
  sourceLabel: Record<string, string> = { local: 'Carpeta local', zip: 'ZIP', git: 'Git' };
  analyzed = computed(() => (this.list.data()?.items ?? []).filter(p => p.lastScan?.['status'] === 'succeeded').length);
  withFindings = computed(() => (this.list.data()?.items ?? []).filter(p => Number(p.lastScan?.['findings'] ?? 0) > 0).length);

  constructor() { this.load(); }

  emptyForm() {
    return { sourceType: '' as SourceType | '', name: '', targetRuntime: 'spring', description: '',
             localPath: '', repositoryUrl: '', defaultBranch: 'main', credentialRef: '' };
  }
  urlOk() { return URL_RE.test(this.form.repositoryUrl.trim()); }
  pathOk() {
    const p = this.form.localPath.trim();
    return p.startsWith('/') && !p.split('/').includes('..') && (this.caps()?.localSourceRoots ?? []).some(r => p === r || p.startsWith(r + '/'));
  }
  sourceReady() {
    switch (this.form.sourceType) {
      case 'local': return this.pathOk();
      case 'zip': return !!this.uploaded() && !this.uploading();
      case 'git': return this.urlOk();
      default: return false;
    }
  }

  async pick(ev: Event) {
    const file = (ev.target as HTMLInputElement).files?.[0];
    this.uploaded.set(null);
    this.createError.set(null);
    if (!file) return;
    const max = (this.caps()?.maxUploadMb ?? 200) * 1024 * 1024;
    if (file.size > max) { this.createError.set(new UiError(413, 'UPLOAD_TOO_LARGE', `El archivo supera ${this.caps()?.maxUploadMb} MB`)); return; }
    this.uploading.set(true);
    try { this.uploaded.set(await this.api.uploadZip(file, file.name)); }
    catch (e) { this.createError.set(UiError.from(e)); }
    finally { this.uploading.set(false); }
  }

  /** Folder from the user's computer: zipped in the browser, then the regular validated ZIP upload. */
  async pickFolder(ev: Event) {
    const list = Array.from((ev.target as HTMLInputElement).files ?? []);
    this.uploaded.set(null);
    this.createError.set(null);
    if (!list.length) return;
    const files = filterPicked(list.map(f => ({ webkitRelativePath: f.webkitRelativePath || f.name, data: f })));
    this.uploading.set(true);
    try {
      const zip = await buildZip(files, { onProgress: (d, t) => this.progress.set(`Comprimiendo ${d}/${t} archivos…`) });
      if (zip.size > (this.caps()?.maxUploadMb ?? 200) * 1024 * 1024) throw new UiError(413, 'UPLOAD_TOO_LARGE', 'La carpeta comprimida supera el máximo permitido');
      this.progress.set('Subiendo y validando estructura…');
      const folder = files[0].path.split('/')[0] || 'proyecto';
      this.uploaded.set(await this.api.uploadZip(zip, `${folder}.zip`));
    } catch (e) {
      this.createError.set(e instanceof Error && !(e instanceof UiError) ? new UiError(422, 'FOLDER_INVALID', e.message) : UiError.from(e));
    } finally {
      this.uploading.set(false);
      this.progress.set('');
    }
  }
  load() { this.list.load(() => this.api.projects({ q: this.q(), limit: 20, cursor: this.pager.current() })); }
  search() { this.pager.reset(); this.load(); }
  openNew() {
    this.form = this.emptyForm();
    this.uploaded.set(null);
    this.createError.set(null);
    this.dialog = true;
    this.api.capabilities().then(c => this.caps.set(c)).catch(e => this.createError.set(UiError.from(e)));
  }
  open(p: Project) { this.ctx.select(p.id); this.router.navigate(['/p', p.id, 'inventory']); }

  async create() {
    this.creating.set(true);
    this.createError.set(null);
    try {
      const f = this.form;
      const common = { name: f.name, sourceType: f.sourceType, targetRuntime: f.targetRuntime, description: f.description };
      // only the fields of the selected source are sent; the API rejects the others
      const body = f.sourceType === 'local' ? { ...common, localPath: f.localPath.trim() }
        : f.sourceType === 'zip' ? { ...common, uploadId: this.uploaded()!.id }
        : { ...common, repositoryUrl: f.repositoryUrl.trim(), defaultBranch: f.defaultBranch || 'main', credentialRef: f.credentialRef || null };
      const p = await this.api.createProject(body);
      this.dialog = false;
      this.open(p);
    } catch (e) {
      this.createError.set(UiError.from(e));
    } finally {
      this.creating.set(false);
    }
  }
}

import { DatePipe } from '@angular/common';
import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { ButtonModule } from 'primeng/button';
import { DialogModule } from 'primeng/dialog';
import { InputTextModule } from 'primeng/inputtext';
import { MessageModule } from 'primeng/message';
import { SelectModule } from 'primeng/select';
import { SelectButtonModule } from 'primeng/selectbutton';
import { TableModule } from 'primeng/table';
import { TagModule } from 'primeng/tag';
import { TextareaModule } from 'primeng/textarea';
import { Api } from '../../core/api';
import { UiError } from '../../core/errors';
import { Capabilities, Page, Project, SourceType, Upload } from '../../core/models';
import { buildZip, filterPicked } from '../../core/zip';
import { FolderBrowser } from './folder-browser';
import { ProjectContext } from '../../core/project-context';
import { AuthService } from '../../core/auth';
import { CursorPager, Loader } from '../../shared/state';
import { PageHeader, StateView, StatusTag, short } from '../../shared/ui';

type Phase = 'pending' | 'running' | 'analyzed' | 'migrated' | 'tested' | 'failed';

const URL_RE = /^https:\/\/[\w.-]+(:443)?\/[\w.\-/~]+$/;

@Component({
  selector: 'mf-portfolio',
  imports: [DatePipe, FormsModule, RouterLink, ButtonModule, DialogModule, InputTextModule, MessageModule, SelectModule, SelectButtonModule, TableModule, TagModule, TextareaModule,
            PageHeader, StateView, StatusTag, FolderBrowser],
  template: `
    <mf-page-header [title]="greeting()" crumb="CARTERA" subtitle="Sus proyectos de migración: abra uno para continuar donde lo dejó o registre uno nuevo.">
      <p-button label="Registrar proyecto" icon="pi pi-plus" (onClick)="openNew()" />
    </mf-page-header>

    <div class="cards">
      <article><i class="pi pi-th-large" aria-hidden="true"></i><b>{{ list.data()?.total ?? '—' }}</b> proyectos visibles</article>
      <article><i class="pi pi-sync" aria-hidden="true"></i><b>{{ countType('JAVA_CAMEL') }}</b> Java / Camel</article>
      <article><i class="pi pi-share-alt" aria-hidden="true"></i><b>{{ countType('DATASTAGE_AIRFLOW') }}</b> DataStage → Airflow</article>
      <article><i class="pi pi-database" aria-hidden="true"></i><b>{{ countType('ODOO') }}</b> Odoo 12 → 19</article>
    </div>
    <div class="phases" role="group" aria-label="Proyectos por etapa">
      @for (ph of phaseOrder; track ph) {
        <button type="button" class="phase" [class.on]="phaseFilter() === ph" (click)="phaseFilter.set(phaseFilter() === ph ? null : ph)"
                [attr.aria-pressed]="phaseFilter() === ph">
          <p-tag [severity]="PHASE[ph].s" [value]="PHASE[ph].label"><i [class]="'pi ' + PHASE[ph].icon" aria-hidden="true" style="margin-right:.35rem"></i></p-tag>
          <b>{{ countPhase(ph) }}</b>
        </button>
      }
      @if (phaseFilter()) { <button type="button" class="link" (click)="phaseFilter.set(null)">Ver todos</button> }
    </div>

    <div class="toolbar">
      <span class="search">
        <i class="pi pi-search" aria-hidden="true"></i>
        <label class="sr-only" for="q">Buscar proyecto</label>
        <input pInputText id="q" placeholder="Buscar proyecto…" [(ngModel)]="q" (keyup.enter)="search()" (search)="search()" type="search" />
      </span>
      <p-selectbutton [options]="typeOptions" optionLabel="label" optionValue="value" [ngModel]="typeFilter()"
                      (ngModelChange)="typeFilter.set($event ?? 'ALL'); search()" aria-label="Filtrar por tipo de migración" />
    </div>

    <mf-state [loading]="list.loading()" [error]="list.error()" [hasData]="!!list.data()" [empty]="list.data()?.items?.length === 0"
              emptyText="No hay proyectos que coincidan. Registre uno nuevo o pida acceso al responsable de un proyecto." (retry)="list.reload()">
      <div class="panel">
        <p-table [value]="shown()" [loading]="list.loading()" dataKey="id" [tableStyle]="{ 'min-width': '48rem', 'table-layout': 'fixed' }" [rowHover]="true">
          <ng-template #header>
            <tr><th scope="col" style="width:34%">Proyecto</th><th scope="col" style="width:22%">Origen</th><th scope="col" style="width:12%">Destino</th>
                <th scope="col" style="width:18%">Estado</th><th scope="col" style="width:10%">Su rol</th><th scope="col" style="width:4%"></th></tr>
          </ng-template>
          <ng-template #body let-p>
            <tr class="prow" (click)="open(p)">
              <td>
                <div class="pname">
                  <span class="picon" [class.ds]="p.migrationType !== 'JAVA_CAMEL'" aria-hidden="true">
                    <i [class]="'pi ' + (typeIcon[p.migrationType] ?? 'pi-sync')"></i></span>
                  <span class="ptext">
                    <a [routerLink]="['/p', p.id, home(p.migrationType)]" (click)="$event.stopPropagation()"><strong>{{ p.name }}</strong></a>
                    <small class="muted">{{ typeLabel(p.migrationType ?? 'JAVA_CAMEL') }}@if (p.description) { · {{ p.description }} }</small>
                  </span>
                </div>
              </td>
              <td><span class="src"><i [class]="'pi ' + sourceIcon[p.sourceType]" aria-hidden="true"></i>
                <span class="mono small" [title]="p.repositoryUrl ?? p.localPath ?? p.upload?.['filename'] ?? ''">{{ sourceText(p) }}</span></span></td>
              <td class="nowrap">{{ runtimeLabel[p.targetRuntime ?? ''] ?? '—' }}</td>
              <td>
                @let ph = phaseOf(p);
                <p-tag [severity]="PHASE[ph].s" [value]="PHASE[ph].label"><i [class]="'pi ' + PHASE[ph].icon" aria-hidden="true" style="margin-right:.35rem"></i></p-tag>
                @if (p.progress?.['step']; as st) {
                  <small class="muted block">{{ st }}@if (p.progress?.['at']; as at) { · {{ at | date: 'd/M/yy HH:mm' }} }</small>
                }
                @if (p.lastDataStage?.['manualJobs']; as mj) { <small class="muted block">{{ mj }} job(s) con trabajo manual</small> }
                @else if (ph === 'analyzed' && p.lastScan?.['findings']) { <small class="muted block">{{ p.lastScan?.['findings'] }} hallazgo(s)</small> }
              </td>
              <td><span class="muted">{{ roleLabel(p.myRole) }}</span></td>
              <td class="go"><i class="pi pi-chevron-right" aria-hidden="true"></i></td>
            </tr>
          </ng-template>
        </p-table>
      </div>
      <div class="pager">
        <p-button label="Anterior" icon="pi pi-chevron-left" [text]="true" [disabled]="pager.page() === 0" (onClick)="pager.prev(); load()" />
        <span class="muted">Página {{ pager.page() + 1 }}</span>
        <p-button label="Siguiente" icon="pi pi-chevron-right" iconPos="right" [text]="true" [disabled]="!list.data()?.nextCursor"
                  (onClick)="pager.next(list.data()?.nextCursor); load()" />
      </div>
    </mf-state>

    <p-dialog header="Registrar proyecto" [(visible)]="dialog" [modal]="true" [style]="{ width: '46rem' }" [breakpoints]="{ '720px': '96vw' }">
      <ol class="wsteps" aria-label="Pasos">
        @for (t of stepTitles; track $index) {
          <li [class.on]="step() === $index + 1" [class.done]="step() > $index + 1"><span>{{ step() > $index + 1 ? '✓' : $index + 1 }}</span> {{ t }}</li>
        }
      </ol>
      <form class="form" (ngSubmit)="create()" #f="ngForm">
        @if (step() === 1) {
          <fieldset class="source">
            <legend>¿De dónde viene el proyecto? <span class="req" aria-hidden="true">*</span></legend>
            @for (o of sourceOptions(); track o.value) {
              <label class="radio" [class.disabled]="o.disabled">
                <input type="radio" name="sourceType" [value]="o.value" [(ngModel)]="form.sourceType" [disabled]="o.disabled" required />
                <span><strong>{{ o.label }}</strong><br /><small class="muted">{{ o.hint }}</small></span>
              </label>
            }
          </fieldset>
          @switch (form.sourceType) {
            @case ('local') {
              <label for="path">Ruta del proyecto</label>
              <span class="row">
                <input pInputText id="path" name="path" required [(ngModel)]="form.localPath" [placeholder]="(caps()?.localSourceRoots?.[0] ?? '/sources') + '/mi-proyecto'" />
                <p-button label="Examinar…" icon="pi pi-folder-open" [outlined]="true" (onClick)="browsing.set(true)" />
              </span>
              <small class="muted">En el equipo donde corre la plataforma, dentro de <code>{{ caps()?.localSourceRoots?.join(', ') }}</code>.
                Se analiza una copia; la carpeta nunca se modifica.</small>
              @if (form.localPath && !pathOk()) { <small class="err">Use una ruta absoluta dentro de un directorio autorizado</small> }
            }
            @case ('zip') {
              <span class="row two">
                <span><label for="zip">Archivo ZIP (máx. {{ caps()?.maxUploadMb ?? 200 }} MB)</label>
                  <input id="zip" type="file" accept=".zip,application/zip" (change)="pick($event)" /></span>
                <span><label for="dir">o una carpeta de mi equipo</label>
                  <input id="dir" type="file" webkitdirectory multiple (change)="pickFolder($event)" /></span>
              </span>
              @if (uploading()) { <small class="muted"><i class="pi pi-spin pi-spinner"></i> {{ progress() || 'Subiendo y revisando el contenido…' }}</small> }
              @if (uploaded(); as u) {
                <p-message severity="success"><span><strong>{{ u.filename }}</strong> · {{ detectedText(u) }}</span></p-message>
              } @else {
                <small class="muted">Proyecto Java/Maven, exportaciones DSX de DataStage o Odoo (respaldo de la base y/o módulos): el tipo se detecta del contenido.</small>
              }
            }
            @case ('git') {
              <label for="url">URL HTTPS del repositorio</label>
              <input pInputText id="url" name="url" required [(ngModel)]="form.repositoryUrl" placeholder="https://github.com/org/repo.git" />
              @if (form.repositoryUrl && !urlOk()) { <small class="err">Solo https://host/ruta (no file://, ssh ni http)</small> }
              <span class="row two">
                <span><label for="branch">Rama o referencia</label><input pInputText id="branch" name="branch" [(ngModel)]="form.defaultBranch" /></span>
                <span><label for="cred">Credencial (opcional)</label>
                  <input pInputText id="cred" name="cred" [(ngModel)]="form.credentialRef" placeholder="env:MF_CRED_GITHUB" /></span>
              </span>
              <small class="muted">Hosts permitidos: {{ caps()?.gitAllowedHosts?.join(', ') }}. Nunca el token: una referencia env:MF_CRED_*.</small>
            }
            @case ('agent') {
              <p-message severity="info"><span>El código <strong>no se sube</strong>. Después de registrar el proyecto, en su equipo (con la CLI de
                Migration Factory) ejecute <code>migrate.py agent push &lt;carpeta&gt; --platform &lt;url&gt; --project-id &lt;id&gt;</code> con su token en
                <code>MF_AGENT_TOKEN</code>: el análisis se hace allí y solo se envían hallazgos, módulos y rutas. La migración también corre en su equipo.</span></p-message>
            }
          }
        }
        @if (step() === 2) {
          @if (detected(); as d) {
            <p-message severity="info"><span>Detectado en el ZIP: <strong>{{ typeLabel(d) }}</strong>. Puede cambiarlo si no es correcto.</span></p-message>
          }
          <fieldset class="source">
            <legend>Tipo de migración</legend>
            @for (t of migrationTypes; track t.value) {
              <label class="radio" [class.disabled]="!!detected() && detected() !== t.value">
                <input type="radio" name="migrationType" [value]="t.value" [(ngModel)]="form.migrationType" (ngModelChange)="typeChanged()"
                       [disabled]="!!detected() && detected() !== t.value" />
                <span><strong>{{ t.label }}</strong><br /><small class="muted">{{ t.hint }}</small></span>
              </label>
            }
          </fieldset>
          @if (form.migrationType === 'JAVA_CAMEL') {
            <label for="rt">Runtime objetivo</label>
            <p-select inputId="rt" name="rt" [options]="runtimes" optionLabel="label" optionValue="value" [(ngModel)]="form.targetRuntime" />
          } @else if (form.migrationType === 'ODOO') {
            <small class="muted">Destino: Odoo 19 Community — la base con OpenUpgrade salto por salto y, si el ZIP los trae, los módulos a la medida portados versión por versión.</small>
          } @else {
            <small class="muted">Destino: Apache Airflow 2.10.5 — DAGs, SQL para Oracle, Connections y Variables, validados con DagBag.</small>
          }
        }
        @if (step() === 3) {
          <label for="name">Nombre del proyecto</label>
          <input pInputText id="name" name="name" required minlength="2" [(ngModel)]="form.name" autofocus />
          <label for="desc">Descripción (opcional)</label>
          <textarea pTextarea id="desc" name="desc" rows="2" [(ngModel)]="form.description"></textarea>
          <dl class="kv review">
            <dt>Origen</dt><dd>{{ sourceLabel[form.sourceType] }} {{ uploaded()?.filename ?? form.repositoryUrl ?? '' }}{{ form.sourceType === 'local' ? form.localPath : '' }}</dd>
            <dt>Tipo</dt><dd>{{ typeLabel(form.migrationType) }} · {{ runtimeLabel[form.targetRuntime] }}</dd>
          </dl>
        }
        @if (createError(); as e) { <p-message severity="error">{{ e.code }}: {{ e.message }}</p-message> }
        <div class="dialog-actions">
          @if (step() > 1) { <p-button label="Atrás" icon="pi pi-arrow-left" [text]="true" (onClick)="step.set(step() - 1)" /> }
          <span class="grow"></span>
          <p-button label="Cancelar" [text]="true" (onClick)="dialog = false" />
          @if (step() < 3) {
            <p-button label="Siguiente" icon="pi pi-arrow-right" iconPos="right" [disabled]="step() === 1 && !sourceReady()" (onClick)="next()" />
          } @else {
            <p-button type="submit" label="Registrar" icon="pi pi-check" [disabled]="!f.valid || !sourceReady()" [loading]="creating()" />
          }
        </div>
      </form>
    </p-dialog>
    <mf-folder-browser [visible]="browsing()" [start]="form.localPath || null" (selected)="form.localPath = $event; browsing.set(false)" (closed)="browsing.set(false)" />`,
  styles: [`.phases{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:-8px 0 18px}
    .phase{display:inline-flex;align-items:center;gap:8px;padding:5px 10px 5px 6px;border:1px solid var(--mf-line);border-radius:10px;background:var(--mf-surface);cursor:pointer;font:inherit}
    .phase.on{border-color:var(--mf-teal);box-shadow:0 0 0 3px var(--mf-teal-soft)}.phase b{font-size:15px}
    .link{background:none;border:0;color:var(--mf-teal);cursor:pointer;font:inherit;text-decoration:underline}.block{display:block}
    .wsteps{display:flex;gap:8px;list-style:none;padding:0;margin:0 0 14px}.wsteps li{flex:1;display:flex;gap:8px;align-items:center;color:var(--mf-muted);
    padding:8px 10px;border-radius:8px;background:#f3f6fa;font-size:13px}.wsteps li span{width:22px;height:22px;border-radius:50%;display:inline-grid;
    place-items:center;background:#dfe7ee;font-weight:600;font-size:12px}.wsteps li.on{background:#e6f2f3;color:var(--mf-ink);font-weight:600}
    .wsteps li.on span{background:var(--mf-teal);color:#fff}.wsteps li.done span{background:#12b76a;color:#fff}.grow{flex:1}
    .review{margin-top:10px;font-size:13px}.chip.ds{background:#fff1e0}
    .search{position:relative;flex:0 1 22rem}.search i{position:absolute;left:12px;top:50%;transform:translateY(-50%);color:#8193a4}
    .search input{width:100%;padding-left:36px}
    .prow{cursor:pointer}.pname{display:flex;gap:12px;align-items:center}.ptext{display:flex;flex-direction:column;min-width:0}
    .ptext small{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:32rem}
    .picon{width:38px;height:38px;flex:0 0 38px;border-radius:11px;display:grid;place-items:center;background:#e3effd;color:#1d4f91}
    .picon.ds{background:#fff0dc;color:#8a4b08}.src{display:inline-flex;gap:8px;align-items:center;max-width:18rem}
    .src .mono{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.block{display:block;margin-top:3px}
    .go{color:#8193a4;text-align:right}.nowrap{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    .ptext small{max-width:100%}.src{max-width:100%}`],
})
export class Portfolio {
  private api = inject(Api);
  private router = inject(Router);
  private ctx = inject(ProjectContext);
  private auth = inject(AuthService);
  list = new Loader<Page<Project>>();
  pager = new CursorPager();
  q = signal('');
  dialog = false;
  step = signal(1);
  stepTitles = ['Origen', 'Tipo', 'Datos'];
  typeFilter = signal<'ALL' | 'JAVA_CAMEL' | 'DATASTAGE_AIRFLOW' | 'ODOO'>('ALL');
  typeOptions = [{ label: 'Todos', value: 'ALL' }, { label: 'Java / Camel', value: 'JAVA_CAMEL' }, { label: 'DataStage → Airflow', value: 'DATASTAGE_AIRFLOW' },
                 { label: 'Odoo 12 → 19', value: 'ODOO' }];
  typeIcon: Record<string, string> = { DATASTAGE_AIRFLOW: 'pi-share-alt', ODOO: 'pi-database', JAVA_CAMEL: 'pi-sync' };
  home(t?: string | null) { return t === 'DATASTAGE_AIRFLOW' ? 'ds' : t === 'ODOO' ? 'odoo' : 'inventory'; }
  runtimeLabel: Record<string, string> = { spring: 'Spring Boot', quarkus: 'Quarkus', airflow: 'Apache Airflow', odoo19: 'Odoo 19 Community' };
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
      { value: 'agent', label: 'Agente local (sin subir código)', disabled: this.form.migrationType !== 'JAVA_CAMEL',
        hint: 'Analiza en su equipo y publica solo el resultado; el código nunca sale de su máquina. Java/Camel.' },
    ];
  });
  runtimes = [{ label: 'Spring Boot', value: 'spring' }, { label: 'Quarkus', value: 'quarkus' }];
  migrationTypes = [
    { value: 'JAVA_CAMEL', label: 'Java / Camel', hint: 'JBoss o Fuse con Camel 2 → Camel 4 (Spring Boot, Quarkus o Tomcat).' },
    { value: 'DATASTAGE_AIRFLOW', label: 'DataStage → Airflow', hint: 'Exportaciones DSX de IBM DataStage → DAGs de Apache Airflow.' },
    { value: 'ODOO', label: 'Odoo 12 → 19', hint: 'Odoo Community 12 → 19: la base con OpenUpgrade (respaldo ZIP de Odoo o pg_dump) y los módulos a la medida.' },
  ];
  short = short;
  sourceLabel: Record<string, string> = { local: 'Carpeta local', zip: 'ZIP', git: 'Git', agent: 'Agente local' };
  /** Etapa de cada proyecto (api.projects.progress): de su última corrida de cualquier tipo. */
  readonly PHASE: Record<Phase, { label: string; s: 'secondary' | 'info' | 'warn' | 'success' | 'danger' | 'contrast'; icon: string }> = {
    pending: { label: 'Sin empezar', s: 'secondary', icon: 'pi-circle' },
    running: { label: 'En curso', s: 'info', icon: 'pi-spin pi-spinner' },
    analyzed: { label: 'Analizado', s: 'warn', icon: 'pi-search' },
    migrated: { label: 'Migrado', s: 'success', icon: 'pi-check' },
    tested: { label: 'Migrado y probado', s: 'success', icon: 'pi-check-circle' },
    failed: { label: 'Con error', s: 'danger', icon: 'pi-times' },
  };
  phaseOrder: Phase[] = ['running', 'analyzed', 'migrated', 'tested', 'failed', 'pending'];
  phaseFilter = signal<Phase | null>(null);
  phaseOf(p: Project): Phase { return ((p.progress?.['phase'] as Phase | undefined) ?? 'pending'); }
  countPhase(ph: Phase) { return (this.list.data()?.items ?? []).filter(p => this.phaseOf(p) === ph).length; }
  shown = computed(() => (this.list.data()?.items ?? []).filter(p => !this.phaseFilter() || this.phaseOf(p) === this.phaseFilter()));
  countType(t: string) { return (this.list.data()?.items ?? []).filter(p => (p.migrationType ?? 'JAVA_CAMEL') === t).length; }
  sourceIcon: Record<string, string> = { local: 'pi-folder', zip: 'pi-file-arrow-up', git: 'pi-github', agent: 'pi-desktop' };
  sourceText(p: Project) {
    const v = p.repositoryUrl ?? p.localPath ?? (p.upload?.['filename'] as string | undefined) ?? '';
    return v.replace(/^https:\/\/(www\.)?/, '').replace(/\.git$/, '');
  }
  roleLabel(r: string | null | undefined) {
    return ({ owner: 'Responsable', architect: 'Arquitecto', developer: 'Desarrollador', reviewer: 'Revisor', auditor: 'Auditor', admin: 'Admin' } as Record<string, string>)[r ?? ''] ?? '—';
  }
  greeting = computed(() => {
    const n = (/^(.*?)\s*\(/.exec(this.auth.me()?.name ?? '')?.[1] ?? this.auth.me()?.name ?? '').split(' ')[0];
    return n ? `Hola, ${n}` : 'Cartera';
  });
  withFindings = computed(() => (this.list.data()?.items ?? []).filter(p => Number(p.lastScan?.['findings'] ?? 0) > 0).length);

  constructor() { this.load(); }

  emptyForm() {
    return { migrationType: 'JAVA_CAMEL' as 'JAVA_CAMEL' | 'DATASTAGE_AIRFLOW' | 'ODOO', sourceType: '' as SourceType | '', name: '', targetRuntime: 'spring', description: '',
             localPath: '', repositoryUrl: '', defaultBranch: 'main', credentialRef: '' };
  }
  typeChanged() {
    this.form.targetRuntime = this.form.migrationType === 'DATASTAGE_AIRFLOW' ? 'airflow' : this.form.migrationType === 'ODOO' ? 'odoo19' : 'spring';
  }
  /** Type found in the uploaded ZIP (.dsx = DataStage, pom.xml = Java/Camel); git and local sources choose it by hand. */
  detected = computed(() => this.uploadedType());
  private uploadedType = computed(() => (this.uploaded()?.structure?.['migrationType'] as 'JAVA_CAMEL' | 'DATASTAGE_AIRFLOW' | 'ODOO' | undefined) ?? null);
  private applyDetected() {
    const t = this.uploadedType();
    if (t && this.form.sourceType === 'zip') { this.form.migrationType = t; this.typeChanged(); }
  }
  next() {
    if (this.step() === 1) this.applyDetected();
    this.step.update(v => Math.min(3, v + 1));
  }
  typeLabel(t: string | null | undefined) { return this.migrationTypes.find(x => x.value === t)?.label ?? '—'; }
  detectedText(u: Upload) {
    const st = u.structure as Record<string, any>;
    if (st['migrationType'] === 'ODOO') {
      const mods = (st['odooModules'] ?? []) as string[];
      const db = st['odooDump'] ? `respaldo ${st['odooDump']}${st['filestore'] ? ' con filestore' : ' (sin filestore: los adjuntos en disco no viajan)'}` : 'sin respaldo de base';
      const sql = (st['odooSql'] ?? []) as string[];
      return `Odoo · ${db}` + (mods.length ? ` · código de ${mods.length} módulo(s): ${mods.slice(0, 5).join(', ')}${mods.length > 5 ? '…' : ''}` : '')
        + (sql.length ? ` · SQL externo (no es la base de Odoo): ${sql.join(', ')}` : '');
    }
    return st['dsxFiles'] ? `DataStage → Airflow · ${st['dsxFiles'].length} exportación(es) DSX`
      : `Java / Camel · raíz Maven ${st['root']} · ${(st['modules'] ?? []).length} módulo(s)`;
  }
  urlOk() { return URL_RE.test(this.form.repositoryUrl.trim()); }
  pathOk() {
    const p = this.form.localPath.trim();
    return p.startsWith('/') && !p.split('/').includes('..') && (this.caps()?.localSourceRoots ?? []).some(r => p === r || p.startsWith(r + '/'));
  }
  sourceReady() {
    switch (this.form.sourceType) {
      case 'agent': return true;
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
    try { this.uploaded.set(await this.api.uploadZip(file, file.name, 'AUTO')); this.applyDetected(); }
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
      this.uploaded.set(await this.api.uploadZip(zip, `${folder}.zip`, 'AUTO'));
      this.applyDetected();
    } catch (e) {
      this.createError.set(e instanceof Error && !(e instanceof UiError) ? new UiError(422, 'FOLDER_INVALID', e.message) : UiError.from(e));
    } finally {
      this.uploading.set(false);
      this.progress.set('');
    }
  }
  load() {
    const t = this.typeFilter();
    this.list.load(() => this.api.projects({ q: this.q(), limit: 20, cursor: this.pager.current(), ...(t === 'ALL' ? {} : { migrationType: t }) }));
  }
  search() { this.pager.reset(); this.load(); }
  openNew() {
    this.form = this.emptyForm();
    this.step.set(1);
    this.uploaded.set(null);
    this.createError.set(null);
    this.dialog = true;
    this.api.capabilities().then(c => this.caps.set(c)).catch(e => this.createError.set(UiError.from(e)));
  }
  open(p: Project) {
    this.ctx.select(p.id);
    this.router.navigate(['/p', p.id, this.home(p.migrationType)]);
  }

  async create() {
    this.creating.set(true);
    this.createError.set(null);
    try {
      const f = this.form;
      const common = { name: f.name, sourceType: f.sourceType, migrationType: f.migrationType, targetRuntime: f.targetRuntime, description: f.description };
      // only the fields of the selected source are sent; the API rejects the others
      const body = f.sourceType === 'local' ? { ...common, localPath: f.localPath.trim() }
        : f.sourceType === 'zip' ? { ...common, uploadId: this.uploaded()!.id }
        : f.sourceType === 'agent' ? common
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

import { Component, computed, inject, signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, NavigationEnd, Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { ButtonModule } from 'primeng/button';
import { TagModule } from 'primeng/tag';
import { ToastModule } from 'primeng/toast';
import { filter, map } from 'rxjs';
import { AuthService } from '../core/auth';
import { ProjectContext } from '../core/project-context';
import { Logo } from '../shared/ui';
import { FlowProgress } from '../shared/flow-progress';

const PROJECT_SCREENS = [
  ['inventory', 'Inventario', 'pi-sitemap'], ['findings', 'Hallazgos', 'pi-exclamation-triangle'], ['plan', 'Plan', 'pi-list-check'],
  ['execution', 'Ejecución', 'pi-cog'], ['diff', 'Diff', 'pi-file-edit'], ['validation', 'Validación', 'pi-verified'],
] as const;
// Pruebas: the original system (JBoss) against the migrated one (Tomcat), as a battery of cases or one request at a time
const TEST_SCREENS = [['automaticas', 'Automáticas', 'pi-sync'], ['manuales', 'Manuales', 'pi-send']] as const;
// DataStage → Airflow projects have their own steps: the Java/Camel screens (findings, plan, diff…) do not apply to them
const DS_SCREENS = [['ds', 'Resumen', 'pi-chart-bar'], ['ds/jobs', 'Jobs', 'pi-list'], ['ds/oleadas', 'Oleadas', 'pi-sitemap'],
  ['ds/acciones', 'Acciones manuales', 'pi-check-square'], ['ds/reconciliacion', 'Reconciliación', 'pi-check-circle']] as const;

@Component({
  selector: 'mf-shell',
  imports: [RouterOutlet, RouterLink, RouterLinkActive, ButtonModule, TagModule, ToastModule, Logo, FlowProgress],
  template: `
    <p-toast position="bottom-right" />
    <a class="skip" href="#main">Saltar al contenido</a>
    <aside class="sidebar" aria-label="Navegación principal">
      <a class="brand" routerLink="/portfolio" aria-label="Migration Factory, ir a Cartera">
        <mf-logo [size]="36" /><span><h2>Migration Factory</h2><p>Integraciones</p></span>
      </a>
      <nav>
        <a routerLink="/portfolio" routerLinkActive="active"><i class="pi pi-th-large" aria-hidden="true"></i> Cartera</a>
        @if (ctx.projectId()) {
          <div class="project-card">
            <small>Proyecto</small>
            <strong [title]="ctx.project()?.name ?? ''">{{ ctx.project()?.name ?? '…' }}</strong>
            @if (ctx.project()) { <span class="type-chip" [class.ds]="isDs() || isOdoo() || isApex()" [class.java]="!isDs() && !isOdoo() && !isApex()">{{ isDs() ? 'DataStage → Airflow' : isOdoo() ? 'Odoo 12 → 19' : isApex() ? 'Oracle APEX' : 'Java / Camel' }}</span> }
          </div>
          @if (isApex()) {
            @for (s of apexScreens; track s[0]) {
              <a [routerLink]="['/p', ctx.projectId(), ...s[0].split('/')]" routerLinkActive="active" [routerLinkActiveOptions]="{ exact: true }">
                <i [class]="'pi ' + s[2]" aria-hidden="true"></i> {{ s[1] }}</a>
            }
          } @else if (isOdoo()) {
            @for (s of odooScreens; track s[0]) {
              <a [routerLink]="['/p', ctx.projectId(), ...s[0].split('/')]" routerLinkActive="active" [routerLinkActiveOptions]="{ exact: true }">
                <i [class]="'pi ' + s[2]" aria-hidden="true"></i> {{ s[1] }}</a>
            }
          } @else if (isDs()) {
            @for (s of dsScreens; track s[0]) {
              <a [routerLink]="['/p', ctx.projectId(), ...s[0].split('/')]" routerLinkActive="active" [routerLinkActiveOptions]="{ exact: true }">
                <i [class]="'pi ' + s[2]" aria-hidden="true"></i> {{ s[1] }}</a>
            }
          } @else {
            @for (s of screens; track s[0]) {
              <a [routerLink]="['/p', ctx.projectId(), s[0]]" routerLinkActive="active"><i [class]="'pi ' + s[2]" aria-hidden="true"></i> {{ s[1] }}</a>
            }
            <div class="nav-group" role="group" aria-labelledby="nav-pruebas">
              <span id="nav-pruebas" class="nav-title">Pruebas</span>
              @for (s of tests; track s[0]) {
                <a [routerLink]="['/p', ctx.projectId(), 'pruebas', s[0]]" routerLinkActive="active"><i [class]="'pi ' + s[2]" aria-hidden="true"></i> {{ s[1] }}</a>
              }
            </div>
          }
        } @else {
          <p class="hint"><i class="pi pi-info-circle" aria-hidden="true"></i> Abra un proyecto en Cartera para ver sus pantallas.</p>
        }
        <div class="bottom">
          <span class="nav-title">Plataforma</span>
          <a routerLink="/rules" routerLinkActive="active"><i class="pi pi-book" aria-hidden="true"></i> Reglas</a>
          <a routerLink="/audit" routerLinkActive="active"><i class="pi pi-history" aria-hidden="true"></i> Auditoría</a>
        </div>
      </nav>
    </aside>
    <div class="main-col">
      <header class="topbar">
        <nav class="trail" aria-label="Ubicación">
          <a routerLink="/portfolio">Cartera</a>
          @if (ctx.project(); as p) {
            <i class="pi pi-chevron-right" aria-hidden="true"></i><strong>{{ p.name }}</strong>
            @if (ctx.role()) { <span class="muted small">· su rol: {{ roleLabel(ctx.role()) }}</span> }
          }
        </nav>
        <span class="user">
          @if (auth.isDemo()) { <span class="demo-pill" title="Usuarios ficticios firmados por la API local">Demostración</span> }
          @if (auth.me()?.isAdmin) { <p-tag severity="contrast" value="admin" /> }
          <span class="avatar" aria-hidden="true">{{ initials() }}</span>
          <span class="who"><strong>{{ displayName() }}</strong><small class="muted">{{ roleText() }}</small></span>
          <p-button icon="pi pi-sign-out" [text]="true" [rounded]="true" severity="secondary" ariaLabel="Salir" (onClick)="auth.logout()" />
        </span>
      </header>
      <main id="main" tabindex="-1">
        @if (ctx.projectId() && !isDs() && !isOdoo() && !isApex() && onProjectScreen()) { <mf-flow-progress /> }
        <router-outlet />
      </main>
    </div>`,
})
export class Shell {
  auth = inject(AuthService);
  ctx = inject(ProjectContext);
  screens = PROJECT_SCREENS;
  tests = TEST_SCREENS;
  dsScreens = DS_SCREENS;
  isDs = computed(() => this.ctx.project()?.migrationType === 'DATASTAGE_AIRFLOW');
  isOdoo = computed(() => this.ctx.project()?.migrationType === 'ODOO');
  isApex = computed(() => this.ctx.project()?.migrationType === 'APEX');
  apexScreens: [string, string, string][] = [['apex', 'Resumen', 'pi-chart-bar'], ['apex/aplicaciones', 'Aplicaciones', 'pi-th-large']];
  odooScreens: [string, string, string][] = [['odoo', 'Resumen', 'pi-chart-bar'], ['odoo/modulos', 'Módulos', 'pi-th-large'], ['odoo/codigo', 'Código', 'pi-code'], ['odoo/pruebas', 'Pruebas', 'pi-check-square']];
  private url = signal('');
  /** the progress bar belongs to the five Java/Camel step screens, not to settings or tests */
  onProjectScreen = computed(() => /^\/p\/[^/]+\/(inventory|findings|plan|execution|diff|validation)/.test(this.url()));
  private nameParts = computed(() => /^(.*?)\s*\((.*)\)\s*$/.exec(this.auth.me()?.name ?? '') ?? [null, this.auth.me()?.name ?? '', '']);
  displayName = computed(() => this.nameParts()[1] || this.auth.me()?.name || '');
  roleText = computed(() => this.nameParts()[2] || (this.auth.me()?.isAdmin ? 'administrador' : ''));
  initials = computed(() => this.displayName().split(/\s+/).map(w => w[0] ?? '').join('').slice(0, 2).toUpperCase() || '?');
  roleLabel(r: string | null) {
    return ({ owner: 'responsable', architect: 'arquitecto', developer: 'desarrollador', reviewer: 'revisor', auditor: 'auditor', admin: 'admin' } as Record<string, string>)[r ?? ''] ?? r;
  }
  private router = inject(Router);

  constructor() {
    // keep the project context in sync with /p/:projectId/... urls
    this.url.set(this.router.url);
    this.router.events.pipe(filter(e => e instanceof NavigationEnd)).subscribe(() => this.url.set(this.router.url));
    this.router.events.pipe(filter(e => e instanceof NavigationEnd), map(() => this.projectFromUrl())).subscribe(id => {
      if (id) this.ctx.select(id);
    });
    const id = this.projectFromUrl();
    if (id) this.ctx.select(id);
  }

  private projectFromUrl() {
    const m = this.router.url.match(/^\/p\/([0-9a-f-]{36})/);
    return m ? m[1] : null;
  }
}

import { Component, computed, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, NavigationEnd, Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { ButtonModule } from 'primeng/button';
import { TagModule } from 'primeng/tag';
import { ToastModule } from 'primeng/toast';
import { filter, map } from 'rxjs';
import { AuthService } from '../core/auth';
import { ProjectContext } from '../core/project-context';

const PROJECT_SCREENS = [
  ['inventory', 'Inventario', 'pi-sitemap'], ['findings', 'Hallazgos', 'pi-exclamation-triangle'], ['plan', 'Plan', 'pi-list-check'],
  ['execution', 'Ejecución', 'pi-cog'], ['diff', 'Diff', 'pi-file-edit'], ['validation', 'Validación', 'pi-verified'],
] as const;

@Component({
  selector: 'mf-shell',
  imports: [RouterOutlet, RouterLink, RouterLinkActive, ButtonModule, TagModule, ToastModule],
  template: `
    <p-toast position="bottom-right" />
    <a class="skip" href="#main">Saltar al contenido</a>
    <aside class="sidebar" aria-label="Navegación principal">
      <div class="brand"><h2>Migration Factory</h2><p>JBoss / Camel 2 → Camel 4</p></div>
      <nav>
        <a routerLink="/portfolio" routerLinkActive="active"><i class="pi pi-briefcase" aria-hidden="true"></i> Cartera</a>
        @for (s of screens; track s[0]) {
          @if (ctx.projectId()) {
            <a [routerLink]="['/p', ctx.projectId(), s[0]]" routerLinkActive="active"><i [class]="'pi ' + s[2]" aria-hidden="true"></i> {{ s[1] }}</a>
          } @else {
            <span class="nav-disabled" title="Seleccione un proyecto en Cartera"><i [class]="'pi ' + s[2]" aria-hidden="true"></i> {{ s[1] }}</span>
          }
        }
        <a routerLink="/rules" routerLinkActive="active"><i class="pi pi-book" aria-hidden="true"></i> Reglas</a>
        <a routerLink="/audit" routerLinkActive="active"><i class="pi pi-history" aria-hidden="true"></i> Auditoría</a>
      </nav>
      @if (!ctx.projectId()) { <p class="hint">Seleccione un proyecto en Cartera para habilitar sus pantallas.</p> }
    </aside>
    <div class="main-col">
      <div class="topbar">
        <span>
          @if (ctx.project(); as p) {
            Proyecto: <a [routerLink]="['/p', p.id, 'settings']"><strong>{{ p.name }}</strong></a>
            <span class="muted"> · rol {{ ctx.role() ?? '—' }}</span>
          } @else { Workspace · Integraciones }
        </span>
        <span class="user">
          @if (auth.isDemo()) { <p-tag severity="warn" value="Modo demostración · usuarios ficticios" /> }
          <span>{{ auth.me()?.name }}</span>
          @if (auth.me()?.isAdmin) { <p-tag severity="contrast" value="admin" /> }
          <p-button label="Salir" icon="pi pi-sign-out" [text]="true" size="small" (onClick)="auth.logout()" />
        </span>
      </div>
      <main id="main" tabindex="-1"><router-outlet /></main>
    </div>`,
})
export class Shell {
  auth = inject(AuthService);
  ctx = inject(ProjectContext);
  screens = PROJECT_SCREENS;
  private router = inject(Router);

  constructor() {
    // keep the project context in sync with /p/:projectId/... urls
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

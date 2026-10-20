import { Component, computed, inject, signal } from '@angular/core';
import { Router } from '@angular/router';
import { ButtonModule } from 'primeng/button';
import { MessageModule } from 'primeng/message';
import { AuthService } from '../../core/auth';
import { UiError } from '../../core/errors';
import { Logo } from '../../shared/ui';

/** What each demo identity can do, so choosing one is an informed choice and not a guess. */
const ROLE_HINT: Record<string, string> = {
  admin: 'Administra la plataforma, catálogos de reglas y perfiles',
  arquitecta: 'Decide sobre hallazgos, aprueba planes y migra',
  desarrollador: 'Analiza, ejecuta previsualizaciones y prueba',
  revisora: 'Revisa y aprueba cambios de forma independiente',
  auditor: 'Consulta todo en modo lectura y la auditoría',
  'sin membresía': 'Sin acceso a proyectos: para probar permisos',
};

@Component({
  selector: 'mf-login',
  imports: [ButtonModule, MessageModule, Logo],
  template: `
    <div class="wrap">
      <section class="hero" aria-label="Migration Factory">
        <div class="brandrow"><mf-logo [size]="44" /><span>Migration Factory</span></div>
        <h1>Moderniza tus integraciones con evidencia, no con suposiciones.</h1>
        <ul class="benefits">
          <li><i class="pi pi-sync" aria-hidden="true"></i><div><strong>Java / Camel</strong><span>JBoss y Fuse con Camel 2 → Camel 4 en Spring Boot, Quarkus o Tomcat.</span></div></li>
          <li><i class="pi pi-share-alt" aria-hidden="true"></i><div><strong>DataStage → Airflow</strong><span>Exportaciones DSX convertidas en DAGs y SQL para Oracle.</span></div></li>
          <li><i class="pi pi-verified" aria-hidden="true"></i><div><strong>Validado y trazable</strong><span>Compilación y pruebas reales, controles de calidad y el original intacto.</span></div></li>
        </ul>
        <p class="foot">El repositorio original nunca se modifica y nada se publica a producción automáticamente.</p>
      </section>

      <section class="access">
        <div class="box">
          <h2>Bienvenido</h2>
          @if (error()) { <p-message severity="error">{{ error()!.message }}</p-message> }
          @if (auth.config()?.mode === 'oidc') {
            <p class="muted">Ingrese con su cuenta corporativa.</p>
            <p-button label="Iniciar sesión" icon="pi pi-sign-in" styleClass="w-full" size="large" (onClick)="auth.oidcLogin()" />
          } @else if (auth.config()?.mode === 'dev') {
            <p class="muted">Elija con qué perfil entrar.</p>
            <div class="demo"><i class="pi pi-info-circle" aria-hidden="true"></i>
              Modo demostración: identidades ficticias firmadas por la API local; no existe en producción.</div>
            <ul class="users">
              @for (u of users(); track u.username) {
                <li>
                  <button type="button" class="user" (click)="login(u.username)" [disabled]="!!busy()" [attr.aria-label]="'Entrar como ' + u.name">
                    <span class="av" [style.background]="u.color">{{ u.initials }}</span>
                    <span class="txt"><strong>{{ u.display }}</strong><small>{{ u.hint }}</small></span>
                    @if (busy() === u.username) { <i class="pi pi-spin pi-spinner" aria-hidden="true"></i> }
                    @else { <i class="pi pi-arrow-right go" aria-hidden="true"></i> }
                  </button>
                </li>
              }
            </ul>
          } @else {
            <p class="muted"><i class="pi pi-spin pi-spinner" aria-hidden="true"></i> Cargando…</p>
          }
        </div>
      </section>
    </div>`,
  styles: [`
    :host{display:block;min-height:100vh}
    .wrap{display:grid;grid-template-columns:minmax(0,1.05fr) minmax(0,1fr);min-height:100vh}
    .hero{background:radial-gradient(120% 90% at 0% 0%,#15506a 0%,#0f2a3d 55%,#0b1f2e 100%);color:#fff;padding:56px 64px;display:flex;flex-direction:column;gap:28px;position:relative;overflow:hidden}
    .hero::after{content:'';position:absolute;right:-120px;bottom:-120px;width:420px;height:420px;border-radius:50%;background:radial-gradient(circle,rgba(47,208,200,.22),transparent 65%)}
    .brandrow{display:flex;gap:12px;align-items:center;font-weight:700;font-size:20px}
    h1{font-size:34px;line-height:1.2;margin:28px 0 4px;max-width:30rem;letter-spacing:-.02em}
    .benefits{list-style:none;padding:0;margin:0;display:grid;gap:18px;max-width:30rem}
    .benefits li{display:flex;gap:14px;align-items:flex-start}
    .benefits i{width:40px;height:40px;flex:0 0 40px;border-radius:12px;display:grid;place-items:center;background:rgba(47,208,200,.16);color:#7ee8e1;font-size:17px}
    .benefits strong{display:block;font-size:15.5px}.benefits span{color:#a9c4d4;font-size:14px}
    .foot{margin-top:auto;color:#8fb0c4;font-size:13px;max-width:30rem}
    .access{display:grid;place-items:center;padding:40px 24px;background:#f4f7fb}
    .box{width:min(100%,27rem)}
    h2{font-size:26px;margin:0 0 4px}
    .demo{display:flex;gap:8px;align-items:flex-start;font-size:12.5px;color:#8a4b08;background:#fff4e5;border:1px solid #ffd8a8;border-radius:12px;padding:10px 12px;margin:14px 0}
    .users{list-style:none;padding:0;margin:0;display:grid;gap:8px}
    .user{width:100%;display:flex;gap:12px;align-items:center;text-align:left;background:#fff;border:1px solid #e2e9f0;border-radius:14px;padding:10px 14px;
      cursor:pointer;font:inherit;color:inherit;transition:box-shadow .15s,border-color .15s,transform .15s}
    .user:hover:not(:disabled){border-color:#9fd6d6;box-shadow:0 6px 20px rgba(16,40,60,.08);transform:translateY(-1px)}
    .user:disabled{opacity:.6;cursor:default}
    .av{width:38px;height:38px;flex:0 0 38px;border-radius:50%;display:grid;place-items:center;color:#fff;font-weight:700;font-size:14px}
    .txt{display:flex;flex-direction:column;flex:1;min-width:0}.txt strong{font-size:14.5px}.txt small{color:#536679;font-size:12.5px}
    .go{color:#8193a4}.user:hover .go{color:#0b8a8f}
    @media (max-width:900px){.wrap{grid-template-columns:1fr}.hero{padding:32px 24px}h1{font-size:26px;margin-top:8px}.foot{display:none}}
  `],
})
export class Login {
  auth = inject(AuthService);
  private router = inject(Router);
  busy = signal<string | null>(null);
  error = signal<UiError | null>(null);
  private colors = ['#0b8a8f', '#2b6cb0', '#7c3aed', '#c2410c', '#0f766e', '#64748b'];
  users = computed(() => ((this.auth.config()?.devUsers ?? []) as { username: string; name: string }[]).map((u, i) => {
    const m = /^(.*?)\s*\((.*)\)\s*$/.exec(u.name);
    const display = m ? m[1] : u.name, role = (m ? m[2] : u.name).toLowerCase();
    const initials = display.split(/\s+/).map(w => w[0]).join('').slice(0, 2).toUpperCase();
    const hint = ROLE_HINT[role] ?? ROLE_HINT[display.toLowerCase().split(' ')[0]] ?? (m ? m[2] : '');
    return { ...u, display: m ? `${display} · ${m[2]}` : display, initials, hint, color: this.colors[i % this.colors.length] };
  }));

  constructor() {
    if (this.auth.token()) this.router.navigate(['/portfolio']);
  }

  async login(username: string) {
    this.busy.set(username);
    try {
      await this.auth.devLogin(username);
      this.router.navigate(['/portfolio']);
    } catch (e) {
      this.error.set(UiError.from(e));
    } finally {
      this.busy.set(null);
    }
  }
}

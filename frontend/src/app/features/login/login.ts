import { Component, inject, signal } from '@angular/core';
import { Router } from '@angular/router';
import { ButtonModule } from 'primeng/button';
import { MessageModule } from 'primeng/message';
import { AuthService } from '../../core/auth';
import { UiError } from '../../core/errors';

@Component({
  selector: 'mf-login',
  imports: [ButtonModule, MessageModule],
  template: `
    <section class="login">
      <h1>Migration Factory</h1>
      <p class="muted">JBoss + Camel 2 → Camel 4 (Spring Boot / Quarkus)</p>
      @if (error()) { <p-message severity="error">{{ error()!.message }}</p-message> }
      @if (auth.config()?.mode === 'oidc') {
        <p-button label="Iniciar sesión con OIDC" icon="pi pi-sign-in" (onClick)="auth.oidcLogin()" />
      } @else if (auth.config()?.mode === 'dev') {
        <p-message severity="warn">Modo demostración: identidades ficticias firmadas por la API local. No disponible en producción.</p-message>
        <ul class="dev-users">
          @for (u of auth.config()!.devUsers; track $index) {
            <li><p-button [label]="$any(u).name" [outlined]="true" (onClick)="login($any(u).username)" [loading]="busy() === $any(u).username" /></li>
          }
        </ul>
      } @else {
        <p class="muted">Cargando configuración de autenticación…</p>
      }
    </section>`,
})
export class Login {
  auth = inject(AuthService);
  private router = inject(Router);
  busy = signal<string | null>(null);
  error = signal<UiError | null>(null);

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

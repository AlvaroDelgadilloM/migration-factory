import { HttpClient, HttpInterceptorFn } from '@angular/common/http';
import { computed, inject, Injectable, signal } from '@angular/core';
import { Router } from '@angular/router';
import { OAuthService } from 'angular-oauth2-oidc';
import { catchError, firstValueFrom, throwError } from 'rxjs';
import { AuthConfig, Me } from './models';

const KEY = 'mf.devToken';

/** Dev mode: fixture users get HS256 tokens from the API. OIDC mode: Authorization Code + PKCE. */
@Injectable({ providedIn: 'root' })
export class AuthService {
  private http = inject(HttpClient);
  private router = inject(Router);
  private oauth = inject(OAuthService);
  readonly config = signal<AuthConfig | null>(null);
  readonly me = signal<Me | null>(null);
  private devToken = signal<string | null>(this.read());
  readonly isDemo = computed(() => this.config()?.mode === 'dev');

  async init() {
    this.config.set(await firstValueFrom(this.http.get<AuthConfig>('/api/v1/auth/config')));
    const c = this.config()!;
    if (c.mode === 'oidc' && c.issuer && c.clientId) {
      this.oauth.configure({
        issuer: c.issuer, clientId: c.clientId, responseType: 'code', scope: 'openid profile email',
        redirectUri: window.location.origin + '/', requireHttps: 'remoteOnly', showDebugInformation: false,
      });
      await this.oauth.loadDiscoveryDocumentAndTryLogin();
      this.oauth.setupAutomaticSilentRefresh();
    }
    if (this.token()) await this.loadMe();
  }

  token(): string | null {
    return this.config()?.mode === 'oidc' ? (this.oauth.hasValidAccessToken() ? this.oauth.getAccessToken() : null) : this.devToken();
  }

  async loadMe() {
    try { this.me.set(await firstValueFrom(this.http.get<Me>('/api/v1/me'))); } catch { this.me.set(null); }
  }

  async devLogin(username: string) {
    const r = await firstValueFrom(this.http.post<{ accessToken: string }>('/api/v1/auth/dev-login', { username }));
    this.devToken.set(r.accessToken);
    try { sessionStorage.setItem(KEY, r.accessToken); } catch { /* storage unavailable: token kept in memory */ }
    await this.loadMe();
  }

  oidcLogin() { this.oauth.initCodeFlow(); }

  logout() {
    this.devToken.set(null);
    this.me.set(null);
    try { sessionStorage.removeItem(KEY); } catch { /* ignore */ }
    if (this.config()?.mode === 'oidc') this.oauth.logOut(true);
    this.router.navigate(['/login']);
  }

  private read(): string | null {
    try { return sessionStorage.getItem(KEY); } catch { return null; }
  }
}

export const authInterceptor: HttpInterceptorFn = (req, next) => {
  const auth = inject(AuthService);
  const token = req.url.startsWith('/api/') ? auth.token() : null;
  const r = token ? req.clone({ setHeaders: { Authorization: `Bearer ${token}` } }) : req;
  return next(r).pipe(catchError(e => {
    if (e?.status === 401 && !req.url.includes('/auth/')) auth.logout();
    return throwError(() => e);
  }));
};

export const authGuard = () => {
  const auth = inject(AuthService);
  return auth.token() ? true : inject(Router).createUrlTree(['/login']);
};

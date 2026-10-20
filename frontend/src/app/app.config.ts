import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { ApplicationConfig, inject, Injectable, provideAppInitializer, provideBrowserGlobalErrorListeners } from '@angular/core';
import { Title } from '@angular/platform-browser';
import { provideRouter, RouterStateSnapshot, TitleStrategy, withComponentInputBinding } from '@angular/router';
import { definePreset } from '@primeuix/themes';
import Aura from '@primeuix/themes/aura';
import { provideOAuthClient } from 'angular-oauth2-oidc';
import { MessageService } from 'primeng/api';
import { providePrimeNG } from 'primeng/config';
import { routes } from './app.routes';
import { AuthService, authInterceptor } from './core/auth';

const Factory = definePreset(Aura, {
  semantic: {
    primary: {
      50: '{teal.50}', 100: '{teal.100}', 200: '{teal.200}', 300: '{teal.300}', 400: '{teal.400}', 500: '{teal.600}',
      600: '{teal.700}', 700: '{teal.800}', 800: '{teal.900}', 900: '{teal.950}', 950: '{teal.950}',
    },
  },
});

const PRODUCT = 'Condor Migration Factory';

/** Browser tab: "<page> · Condor Migration Factory" (the route titles name only the page). */
@Injectable({ providedIn: 'root' })
export class ProductTitle extends TitleStrategy {
  private title = inject(Title);
  override updateTitle(state: RouterStateSnapshot) {
    const page = this.buildTitle(state);
    this.title.setTitle(page && !page.includes(PRODUCT) ? `${page} · ${PRODUCT}` : page ?? PRODUCT);
  }
}

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideRouter(routes, withComponentInputBinding()),
    provideHttpClient(withInterceptors([authInterceptor])),
    provideOAuthClient(),
    providePrimeNG({ theme: { preset: Factory, options: { darkModeSelector: false } } }),
    MessageService,
    { provide: TitleStrategy, useClass: ProductTitle },
    provideAppInitializer(() => inject(AuthService).init()),
  ],
};

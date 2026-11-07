import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { ApplicationConfig, inject, provideAppInitializer, provideBrowserGlobalErrorListeners } from '@angular/core';
import { provideRouter, withComponentInputBinding } from '@angular/router';
import { definePreset } from '@primeuix/themes';
import Aura from '@primeuix/themes/aura';
import { provideOAuthClient } from 'angular-oauth2-oidc';
import { MessageService } from 'primeng/api';
import { providePrimeNG } from 'primeng/config';
import { routes } from './app.routes';
import { AuthService, authInterceptor } from './core/auth';

/** Paleta del logo (el cóndor: dorado #F4BD28 sobre carbón #1C2833). El primario es el ámbar del logo; el texto
 *  que va sobre él es carbón (8.68:1), nunca blanco (1.73:1). Los usos de primario como texto sobre fondo claro
 *  usan el ámbar oscuro (#8a5a00, 5.93:1 sobre blanco) vía highlight / bordes de foco, no el ámbar brillante. */
const Factory = definePreset(Aura, {
  semantic: {
    primary: {
      50: '#fffbeb', 100: '#fef3c7', 200: '#fde68a', 300: '#fcd34d', 400: '#fbbf24', 500: '#F4BD28',
      600: '#d9a41c', 700: '#b4830f', 800: '#8a5a00', 900: '#6b4e00', 950: '#451a03',
    },
    focusRing: { color: '#1C2833' },
    colorScheme: {
      light: {
        primary: { color: '{primary.500}', contrastColor: '#1C2833', hoverColor: '{primary.600}', activeColor: '{primary.700}' },
        highlight: { background: '{primary.100}', focusBackground: '{primary.200}', color: '{primary.800}', focusColor: '{primary.900}' },
        formField: { focusBorderColor: '{primary.800}', floatLabelFocusColor: '{primary.800}' },
      },
    },
  },
});

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideRouter(routes, withComponentInputBinding()),
    provideHttpClient(withInterceptors([authInterceptor])),
    provideOAuthClient(),
    providePrimeNG({ theme: { preset: Factory, options: { darkModeSelector: false } } }),
    MessageService,
    provideAppInitializer(() => inject(AuthService).init()),
  ],
};

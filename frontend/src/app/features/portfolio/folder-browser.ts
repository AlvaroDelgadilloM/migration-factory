import { Component, inject, input, output, signal, effect } from '@angular/core';
import { ButtonModule } from 'primeng/button';
import { DialogModule } from 'primeng/dialog';
import { MessageModule } from 'primeng/message';
import { Api } from '../../core/api';
import { UiError } from '../../core/errors';
import { Browse } from '../../core/models';

/** Server-side explorer: folders as the worker sees them, limited to the authorized roots. */
@Component({
  selector: 'mf-folder-browser',
  imports: [ButtonModule, DialogModule, MessageModule],
  template: `
    <p-dialog header="Seleccionar carpeta (equipo del worker)" [visible]="visible()" (visibleChange)="!$event && closed.emit()" [modal]="true" [style]="{ width: '38rem' }">
      <p class="muted small">Solo directorios autorizados. Las carpetas con <span class="chip">Maven</span> contienen pom.xml.</p>
      @if (error(); as e) { <p-message severity="error">{{ e.code }}: {{ e.message }}</p-message> }
      @if (view(); as v) {
        <div class="crumbbar">
          <p-button icon="pi pi-arrow-up" [text]="true" size="small" ariaLabel="Subir un nivel" [disabled]="!v.path" (onClick)="open(v.parent)" />
          <code>{{ v.path ?? 'Directorios autorizados' }}</code>
        </div>
        <ul class="folders" role="listbox" aria-label="Carpetas">
          @for (e of v.entries; track e.path) {
            <li><button type="button" class="link folder" (click)="open(e.path)" (dblclick)="open(e.path)">
              <i class="pi pi-folder" aria-hidden="true"></i> {{ e.name }} @if (e.hasPom) { <span class="chip">Maven</span> }</button></li>
          } @empty { <li class="muted">Sin subcarpetas</li> }
        </ul>
        @if (v.truncated) { <small class="muted">Se muestran las primeras 500 carpetas.</small> }
        <div class="dialog-actions">
          <p-button label="Cancelar" [text]="true" (onClick)="closed.emit()" />
          <p-button label="Seleccionar esta carpeta" icon="pi pi-check" [disabled]="!v.path" (onClick)="selected.emit(v.path!)" />
        </div>
        @if (v.path && !v.hasPom) { <small class="muted">Esta carpeta no tiene pom.xml en su raíz; el registro buscará el pom.xml menos profundo.</small> }
      }
    </p-dialog>`,
})
export class FolderBrowser {
  private api = inject(Api);
  visible = input(false);
  start = input<string | null>(null);
  selected = output<string>();
  closed = output<void>();
  view = signal<Browse | null>(null);
  error = signal<UiError | null>(null);

  constructor() {
    effect(() => { if (this.visible()) this.open(this.start() || null); });
  }

  async open(path: string | null | undefined) {
    this.error.set(null);
    try { this.view.set(await this.api.browse(path ?? null)); }
    catch (e) { this.error.set(UiError.from(e)); if (path) this.view.set(await this.api.browse(null).catch(() => null)); }
  }
}

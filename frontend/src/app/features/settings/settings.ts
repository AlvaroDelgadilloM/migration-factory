import { Component, effect, inject, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ButtonModule } from 'primeng/button';
import { CheckboxModule } from 'primeng/checkbox';
import { InputTextModule } from 'primeng/inputtext';
import { MessageModule } from 'primeng/message';
import { MultiSelectModule } from 'primeng/multiselect';
import { SelectModule } from 'primeng/select';
import { TableModule } from 'primeng/table';
import { Api } from '../../core/api';
import { UiError } from '../../core/errors';
import { Member } from '../../core/models';
import { ProjectContext } from '../../core/project-context';
import { Loader } from '../../shared/state';
import { ActionButton, PageHeader, StateView } from '../../shared/ui';

@Component({
  selector: 'mf-settings',
  imports: [FormsModule, ButtonModule, CheckboxModule, InputTextModule, MessageModule, MultiSelectModule, SelectModule, TableModule, ActionButton, PageHeader, StateView],
  template: `
    <mf-page-header title="Proyecto" crumb="PROYECTO" [subtitle]="ctx.project()?.name" />
    @if (error(); as e) { <p-message severity="error">{{ e.code }}: {{ e.message }}</p-message> }
    @if (saved()) { <p-message severity="success">Cambios guardados</p-message> }
    @if (ctx.project(); as p) {
      <dl class="kv"><dt>Origen</dt><dd>{{ p.sourceType === 'git' ? 'Repositorio Git' : p.sourceType === 'zip' ? 'Archivo ZIP' : 'Carpeta local' }}</dd>
        @switch (p.sourceType) {
          @case ('git') { <dt>Repositorio</dt><dd class="mono">{{ p.repositoryUrl }} ({{ p.repositoryProvider }})</dd>
            <dt>Credencial</dt><dd class="mono">{{ p.credentialRef ?? 'sin credencial' }}</dd><dt>Rama</dt><dd>{{ p.defaultBranch }}</dd> }
          @case ('local') { <dt>Ruta (worker)</dt><dd class="mono">{{ p.localPath }}</dd> }
          @case ('zip') { <dt>Archivo</dt><dd class="mono">{{ p.upload?.['filename'] }} · sha256 {{ $any(p.upload?.['sha256'])?.slice(0, 16) }}…</dd> }
        }</dl>
      <h2>Gates obligatorios y revisión</h2>
      <form class="form narrow" (ngSubmit)="save()">
        <label for="gates">Gates obligatorios para PR</label>
        <p-multiselect inputId="gates" name="gates" [options]="allGates" [(ngModel)]="gates" [disabled]="!ctx.can('manage')" />
        <span class="inline"><p-checkbox inputId="ind" name="ind" [binary]="true" [(ngModel)]="independent" [disabled]="!ctx.can('manage')" />
          <label for="ind">Revisión independiente (quien ejecuta no aprueba su diff ni registra su evidencia)</label></span>
        <h3>PR borrador (GitHub)</h3>
        @if (ctx.project()?.sourceType !== 'git') { <p class="muted">Ramas y PR solo están disponibles cuando el origen es Git. El candidato se descarga como ZIP.</p> }
        <label for="repo">Repositorio destino (owner/nombre)</label><input pInputText id="repo" name="repo" [(ngModel)]="pr.repository" [disabled]="!ctx.can('manage')" />
        <label for="base">Rama base</label><input pInputText id="base" name="base" [(ngModel)]="pr.baseBranch" [disabled]="!ctx.can('manage')" />
        <label for="cr">Referencia de credencial</label><input pInputText id="cr" name="cr" [(ngModel)]="pr.credentialRef" placeholder="env:MF_CRED_GITHUB" [disabled]="!ctx.can('manage')" />
        <small class="muted">Solo GitHub está implementado. La integración real requiere un token con permisos de PR configurado en el worker.</small>
        <mf-action label="Guardar" icon="pi pi-save" [disabledReason]="ctx.can('manage') ? null : 'Solo el owner configura el proyecto'" (run)="save()" />
      </form>
    }
    <h2>Miembros</h2>
    <mf-state [loading]="members.loading()" [error]="members.error()" [hasData]="!!members.data()" (retry)="members.reload()">
      <p-table [value]="members.data() ?? []">
        <ng-template #header><tr><th scope="col">Sujeto</th><th scope="col">Nombre</th><th scope="col">Rol</th><th scope="col"></th></tr></ng-template>
        <ng-template #body let-m><tr><td class="mono">{{ m.subjectId }}</td><td>{{ m.displayName }}</td><td>{{ m.role }}</td>
          <td><p-button icon="pi pi-trash" [text]="true" severity="danger" [disabled]="!ctx.can('manage')" [ariaLabel]="'Retirar ' + m.subjectId" (onClick)="remove(m)" /></td></tr></ng-template>
      </p-table>
      @if (ctx.can('manage')) {
        <form class="toolbar" (ngSubmit)="add()">
          <label for="sub">Sujeto (OIDC sub)</label><input pInputText id="sub" name="sub" [(ngModel)]="newSubject" />
          <p-select inputId="role" name="role" [options]="roles" [(ngModel)]="newRole" ariaLabel="Rol" />
          <p-button type="submit" label="Añadir / cambiar rol" icon="pi pi-user-plus" [disabled]="!newSubject" />
        </form>
      }
    </mf-state>`,
})
export class Settings {
  projectId = input.required<string>();
  ctx = inject(ProjectContext);
  private api = inject(Api);
  members = new Loader<Member[]>();
  error = signal<UiError | null>(null);
  saved = signal(false);
  allGates = ['G0', 'G1', 'G2', 'G3', 'G4', 'G5', 'G6'];
  roles = ['owner', 'architect', 'developer', 'reviewer', 'auditor'];
  gates: string[] = [];
  independent = true;
  pr = { repository: '', baseBranch: 'main', credentialRef: '' };
  newSubject = '';
  newRole = 'developer';

  constructor() {
    effect(() => { const id = this.projectId(); this.ctx.select(id); this.members.load(() => this.api.members(id)); });
    effect(() => {
      const p = this.ctx.project();
      if (!p) return;
      this.gates = [...p.requiredGates];
      this.independent = p.independentReview;
      const c = (p.prConfig ?? {}) as Record<string, string>;
      this.pr = { repository: c['repository'] ?? '', baseBranch: c['baseBranch'] ?? 'main', credentialRef: c['credentialRef'] ?? '' };
    });
  }

  async save() {
    const p = this.ctx.project();
    if (!p) return;
    this.error.set(null);
    this.saved.set(false);
    const body: Record<string, unknown> = { requiredGates: this.gates, independentReview: this.independent };
    if (this.pr.repository) body['prConfig'] = { provider: 'github', ...this.pr };
    try { await this.api.updateProject(p.id, p.version, body); await this.ctx.refresh(); this.saved.set(true); }
    catch (e) { this.error.set(UiError.from(e)); }
  }

  async add() {
    try { await this.api.setMember(this.projectId(), this.newSubject.trim(), this.newRole); this.newSubject = ''; this.members.reload(); }
    catch (e) { this.error.set(UiError.from(e)); }
  }

  async remove(m: Member) {
    try { await this.api.removeMember(this.projectId(), m.subjectId); this.members.reload(); }
    catch (e) { this.error.set(UiError.from(e)); }
  }
}

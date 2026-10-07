import { Component, computed, effect, inject, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { MessageModule } from 'primeng/message';
import { SelectModule } from 'primeng/select';
import { TableModule } from 'primeng/table';
import { Api } from '../../core/api';
import { UiError } from '../../core/errors';
import { EffortEstimate, Page, Plan, PlanStep, Profile, Scan } from '../../core/models';
import { ProjectContext } from '../../core/project-context';
import { Loader } from '../../shared/state';
import { ActionButton, PageHeader, StateView, StatusTag, TrafficLight, short } from '../../shared/ui';

@Component({
  selector: 'mf-plan',
  imports: [FormsModule, MessageModule, SelectModule, TableModule, ActionButton, PageHeader, StateView, StatusTag, TrafficLight],
  template: `
    <mf-page-header title="Plan" crumb="PLAN" subtitle="Perfil objetivo versionado, pasos, precondiciones y bloqueos" />
    <mf-state [loading]="scans.loading()" [error]="scans.error()" [hasData]="!!scans.data()" [empty]="!scanId()"
              emptyText="Se necesita un análisis correcto antes de planificar." (retry)="scans.reload()">
      <div class="toolbar">
        <label for="sc">Análisis</label>
        <p-select inputId="sc" [options]="succeeded()" optionValue="id" [ngModel]="scanId()" (ngModelChange)="scanId.set($event)">
          <ng-template #selectedItem let-s>{{ scanLabel(s) }}</ng-template><ng-template #item let-s>{{ scanLabel(s) }}</ng-template></p-select>
        <label for="pf">Perfil objetivo</label>
        <p-select inputId="pf" [options]="profiles.data() ?? []" optionLabel="name" optionValue="id" [(ngModel)]="profileId" [style]="{ minWidth: '24rem' }">
          <ng-template #item let-p>{{ p.name }} · v{{ p.version }} · {{ p.status }}</ng-template></p-select>
        <mf-action label="Crear plan" icon="pi pi-plus" [busy]="busy() === 'create'" [disabledReason]="createReason()" (run)="create()" />
        <label for="pv">Versión</label>
        <p-select inputId="pv" [options]="plans.data() ?? []" optionValue="id" [ngModel]="planId()" (ngModelChange)="planId.set($event)" placeholder="Sin planes">
          <ng-template #selectedItem let-p>v{{ p.version }} · {{ p.status }}</ng-template><ng-template #item let-p>v{{ p.version }} · {{ p.status }}</ng-template></p-select>
      </div>
      <p class="muted">Versiones de perfil fijadas y verificadas contra Maven Central; ver Reglas → Perfiles. Sin porcentajes de compatibilidad o ahorro.</p>
      @if (actionError(); as e) {
        <p-message severity="error">{{ e.code }}: {{ e.message }}
          @if (e.details['blocked']) { <ul>@for (b of blockedDetails(e); track b.key) { <li>{{ b.key }}: {{ b.reasons.join('; ') }}</li> }</ul> }
        </p-message>
      }

      <h2>Estimación de esfuerzo</h2>
      <mf-state [loading]="effort.loading()" [error]="effort.error()" [hasData]="!!effort.data()" (retry)="effort.reload()">
        @if (effort.data(); as est) {
          <p class="muted">{{ est.note }} {{ est.diffBasis }}.</p>
          <p-table [value]="est.rows" dataKey="category">
            <ng-template #header><tr><th scope="col">Categoría</th><th scope="col">Unidad</th><th scope="col">Unidades</th>
              <th scope="col">Horas/unidad</th><th scope="col">Fuente</th><th scope="col">Subtotal (h)</th></tr></ng-template>
            <ng-template #body let-r><tr><td>{{ r.label }}</td><td>{{ r.unit }}</td><td>{{ r.units }}</td>
              <td>{{ r.hoursPerUnit ?? '—' }}</td><td><small>{{ r.source }}</small></td><td>{{ r.subtotalHours ?? '—' }}</td></tr></ng-template>
          </p-table>
          <p><strong>Horas conocidas: {{ est.knownHours }}</strong>
            @if (!est.complete) { <span class="muted"> · incompleto: {{ est.uncalibrated.length }} categoría(s) sin calibrar
              (defina horas en rules/effort.json o registre minutos al revisar hallazgos y archivos del diff)</span> }</p>
        }
      </mf-state>

      <mf-state [loading]="plan.loading()" [error]="plan.error()" [hasData]="!!plan.data()" [empty]="!planId()"
                emptyText="Aún no hay planes para este análisis." (retry)="plan.reload()">
        @if (plan.data(); as p) {
          <div class="plan-head">
            <span>Plan v{{ p.version }} <mf-status [value]="p.status" /> · perfil <strong>{{ p.profile?.['name'] }}</strong> v{{ p.profile?.['version'] }}
              · digest <code>{{ p.digest.slice(0, 12) }}</code>
              @if (p.approvedBy) { · aprobado por {{ p.approvedBy }} }</span>
            <span class="actions">
              <mf-action label="Aprobar" icon="pi pi-check" severity="success" [busy]="busy() === 'approve'" [disabledReason]="approveReason()" (run)="approve(p)" />
              <mf-action label="Generar preview" icon="pi pi-eye" [outlined]="true" [busy]="busy() === 'preview'" [disabledReason]="runReason('preview')" (run)="run(p, 'preview')" />
              <mf-action label="Ejecutar en copia aislada" icon="pi pi-play" [busy]="busy() === 'apply'" [disabledReason]="runReason('apply')" (run)="run(p, 'apply')" />
            </span>
          </div>
          <div class="cards">
            <article><b>{{ count('ready', 'recipe') }}</b> pasos automatizables listos</article>
            <article><b>{{ count('blocked') }}</b> pasos bloqueados</article>
            <article><b>{{ count('pending', 'manual') + count('blocked', 'manual') }}</b> decisiones manuales pendientes</article>
          </div>
          <p-table [value]="steps()" dataKey="key" [expandedRowKeys]="expanded">
            <ng-template #header><tr><th></th><th scope="col">#</th><th scope="col">Paso</th><th scope="col">Tipo</th><th scope="col">Clase</th>
              <th scope="col">Gate</th><th scope="col">Estado</th><th scope="col">Bloqueos / dependencias</th></tr></ng-template>
            <ng-template #body let-s let-exp="expanded">
              <tr>
                <td><button type="button" class="link" [pRowToggler]="s" [attr.aria-label]="'Detalle de ' + s.key"><i [class]="exp ? 'pi pi-chevron-down' : 'pi pi-chevron-right'"></i></button></td>
                <td>{{ s.ordinal }}</td><td><strong>{{ s.title }}</strong><br /><small class="mono muted">{{ s.key }}</small></td>
                <td>{{ s.kind }}</td><td><mf-light [classification]="s.classification" [state]="s.state" /> <small>{{ s.classification }}</small></td><td>{{ s.gate ?? '—' }}</td><td><mf-status [value]="s.state" /></td>
                <td><small>@for (r of s.blockedReasons; track r) { <div class="err">{{ r }}</div> }
                  @if (s.dependsOn.length) { <div class="muted">depende de: {{ s.dependsOn.join(', ') }}</div> }
                  @if (s.findingIds.length) { <div class="muted">{{ s.findingIds.length }} hallazgo(s) vinculados</div> }</small></td>
              </tr>
            </ng-template>
            <ng-template #expandedrow let-s>
              <tr><td colspan="8" class="step-detail">
                @if (s.preconditions.length) {
                  <h4>Precondiciones</h4>
                  <ul>@for (c of s.preconditions; track c.code) { <li><i [class]="c.ok ? 'pi pi-check ok' : 'pi pi-times err'" aria-hidden="true"></i>
                    {{ c.ok ? 'Cumple' : 'No cumple' }}: {{ c.description }} <small class="muted">{{ c.detail }}</small></li> }</ul>
                }
                @if (s.notes.length) { <h4>Notas</h4><ul>@for (n of s.notes; track n) { <li>{{ n }}</li> }</ul> }
                @if (s.recipe?.activeRecipes?.length) {
                  <h4>Recetas OpenRewrite</h4><p class="mono small">{{ s.recipe.activeRecipes.join(', ') }}</p>
                  @if (s.recipe.yaml) { <pre class="log">{{ s.recipe.yaml }}</pre> }
                }
                @if (s.recipe?.edits?.length) {
                  <h4>Ediciones localizadas</h4>
                  <ul>@for (e of s.recipe.edits; track $index) { <li class="mono small">{{ e.op }} · {{ e.file }}:{{ e.line }} @if (e.old) { · {{ e.old }} → {{ e.new }} }</li> }</ul>
                }
                @if (s.rollback) { <h4>Rollback</h4><p>{{ s.rollback }}</p> }
              </td></tr>
            </ng-template>
          </p-table>
        }
      </mf-state>
    </mf-state>`,
})
export class PlanPage {
  projectId = input.required<string>();
  ctx = inject(ProjectContext);
  private api = inject(Api);
  private router = inject(Router);
  scans = new Loader<Page<Scan>>();
  profiles = new Loader<Profile[]>();
  plans = new Loader<Plan[]>();
  plan = new Loader<Plan>();
  effort = new Loader<EffortEstimate>();
  scanId = signal<string | null>(null);
  planId = signal<string | null>(null);
  profileId: string | null = null;
  busy = signal<string | null>(null);
  actionError = signal<UiError | null>(null);
  expanded: Record<string, boolean> = {};
  succeeded = computed(() => (this.scans.data()?.items ?? []).filter(s => s.status === 'succeeded'));
  steps = computed(() => (this.plan.data()?.steps ?? []) as unknown as PlanStep[]);
  createReason = computed(() => !this.ctx.perms() ? 'Cargando permisos…' : !this.ctx.can('plan') ? `Su rol (${this.ctx.role()}) no permite crear planes` : null);
  approveReason = computed(() => {
    const p = this.plan.data();
    if (!this.ctx.can('plan')) return `Su rol (${this.ctx.role()}) no permite aprobar`;
    return p?.status === 'draft' ? null : `El plan está ${p?.status}`;
  });

  constructor() {
    effect(async () => {
      const id = this.projectId();
      this.ctx.select(id);
      this.profiles.load(() => this.api.profiles({ status: 'validated' })).then(ps => {
        const rt = this.ctx.project()?.targetRuntime;
        this.profileId = ps?.find(p => p.runtime === rt)?.id ?? ps?.[0]?.id ?? null;
      });
      const page = await this.scans.load(() => this.api.scans(id, { limit: 50 }));
      this.scanId.set(page?.items.find(s => s.status === 'succeeded')?.id ?? null);
    });
    effect(() => { const s = this.scanId(); if (s) { this.loadPlans(s); this.effort.load(() => this.api.effortEstimate(s)); } });
    effect(() => { const p = this.planId(); if (p) this.plan.load(() => this.api.plan(p)); });
  }

  scanLabel(s: Scan) { return `${new Date(s.createdAt).toLocaleString()} · ${short(s.commitSha)}`; }
  count(state: string, kind?: string) { return this.steps().filter(s => s.state === state && (!kind || s.kind === kind)).length; }
  blockedDetails(e: UiError) { return e.details['blocked'] as { key: string; reasons: string[] }[]; }

  runReason(mode: 'preview' | 'apply') {
    const p = this.plan.data();
    if (!this.ctx.can('execute')) return `Su rol (${this.ctx.role()}) no permite ejecutar`;
    if (!p) return 'Sin plan';
    if (p.status === 'superseded') return 'Plan reemplazado por una versión posterior';
    if (p.profile?.['status'] !== 'validated') return 'El perfil objetivo no está validado';
    if (mode === 'apply' && p.status !== 'approved') return 'Requiere plan aprobado';
    if (this.count('ready', 'recipe') === 0) return 'Ningún paso automatizable está listo: resuelva los bloqueos';
    return null;
  }

  async loadPlans(scanId: string) {
    const list = await this.plans.load(() => this.api.plans(scanId));
    this.planId.set(list?.[0]?.id ?? null);
  }

  private async act(name: string, fn: () => Promise<unknown>) {
    this.busy.set(name);
    this.actionError.set(null);
    try { return await fn(); } catch (e) { this.actionError.set(UiError.from(e)); return null; } finally { this.busy.set(null); }
  }

  async create() {
    if (!this.profileId || !this.scanId()) return;
    const p = await this.act('create', () => this.api.createPlan(this.scanId()!, this.profileId!)) as Plan | null;
    if (p) { await this.loadPlans(this.scanId()!); this.planId.set(p.id); }
  }

  async approve(p: Plan) {
    if (await this.act('approve', () => this.api.approvePlan(p.id, p.rowVersion))) { this.plan.reload(); this.plans.reload(); }
  }

  async run(p: Plan, mode: 'preview' | 'apply') {
    const r = await this.act(mode, () => this.api.startExecution(p.id, mode)) as { executionId: string } | null;
    if (r) this.router.navigate(['/p', this.projectId(), 'execution'], { queryParams: { executionId: r.executionId } });
  }
}

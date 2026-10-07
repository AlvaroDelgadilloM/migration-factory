import { Component, computed, effect, ElementRef, inject, input, OnDestroy, output, signal, viewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ButtonModule } from 'primeng/button';
import { MessageModule } from 'primeng/message';
import { ProgressBarModule } from 'primeng/progressbar';
import { SkeletonModule } from 'primeng/skeleton';
import { TagModule } from 'primeng/tag';
import { ToggleSwitchModule } from 'primeng/toggleswitch';
import { Api } from '../core/api';
import { AuthService } from '../core/auth';
import { UiError } from '../core/errors';
import { streamEvents } from '../core/sse';

@Component({
  selector: 'mf-page-header',
  template: `
    <header class="page-header">
      <div>
        <small class="crumb">MIGRATION FACTORY / {{ crumb() }}</small>
        <h1>{{ title() }}</h1>
        @if (subtitle()) { <p class="muted">{{ subtitle() }}</p> }
      </div>
      <div class="actions"><ng-content /></div>
    </header>`,
})
export class PageHeader {
  title = input.required<string>();
  crumb = input('');
  subtitle = input<string | null | undefined>('');
}

/** Uniform loading / error (with requestId + retry) / forbidden / empty states. */
@Component({
  selector: 'mf-state',
  imports: [ButtonModule, MessageModule, SkeletonModule],
  template: `
    @if (error(); as e) {
      @if (e.forbidden) {
        <p-message severity="warn" styleClass="state-msg">
          <span><i class="pi pi-lock" aria-hidden="true"></i> Permisos insuficientes: {{ e.message }}</span>
        </p-message>
      } @else {
        <p-message severity="error" styleClass="state-msg">
          <span><strong>{{ e.code }}</strong> — {{ e.message }}
            @if (e.requestId) { <small class="mono">(requestId {{ e.requestId }})</small> }</span>
          <p-button label="Reintentar" icon="pi pi-refresh" size="small" [text]="true" (onClick)="retry.emit()" />
        </p-message>
      }
    } @else if (loading() && !hasData()) {
      <div aria-busy="true" aria-label="Cargando"><p-skeleton height="2.2rem" styleClass="mb-2" /><p-skeleton height="2.2rem" styleClass="mb-2" /><p-skeleton height="2.2rem" /></div>
    } @else if (empty()) {
      <div class="empty" role="status"><i class="pi pi-inbox" aria-hidden="true"></i> {{ emptyText() }}</div>
    } @else {
      <ng-content />
    }`,
})
export class StateView {
  loading = input(false);
  error = input<UiError | null>(null);
  empty = input(false);
  hasData = input(false);
  emptyText = input('Sin datos');
  retry = output<void>();
}

const SEVERITY: Record<string, { s: 'success' | 'info' | 'warn' | 'danger' | 'secondary' | 'contrast'; icon: string }> = {
  succeeded: { s: 'success', icon: 'pi-check' }, PASS: { s: 'success', icon: 'pi-check' }, approved: { s: 'success', icon: 'pi-check' },
  applied: { s: 'success', icon: 'pi-check' }, ready: { s: 'success', icon: 'pi-play' }, cleared: { s: 'success', icon: 'pi-check' },
  resolved: { s: 'success', icon: 'pi-check' }, validated: { s: 'success', icon: 'pi-verified' }, active: { s: 'success', icon: 'pi-verified' },
  running: { s: 'info', icon: 'pi-spin pi-spinner' }, queued: { s: 'info', icon: 'pi-clock' }, previewed: { s: 'info', icon: 'pi-eye' },
  proposed: { s: 'info', icon: 'pi-comment' }, draft: { s: 'secondary', icon: 'pi-pencil' }, pending: { s: 'secondary', icon: 'pi-clock' },
  open: { s: 'warn', icon: 'pi-circle' }, NOT_RUN: { s: 'secondary', icon: 'pi-minus-circle' }, SKIPPED: { s: 'secondary', icon: 'pi-forward' },
  skipped: { s: 'secondary', icon: 'pi-forward' }, 'no-changes': { s: 'secondary', icon: 'pi-minus' }, superseded: { s: 'secondary', icon: 'pi-history' },
  discarded: { s: 'secondary', icon: 'pi-ban' }, retired: { s: 'secondary', icon: 'pi-ban' }, cancelled: { s: 'warn', icon: 'pi-ban' },
  rolled_back: { s: 'warn', icon: 'pi-undo' }, blocked: { s: 'danger', icon: 'pi-lock' }, FAIL: { s: 'danger', icon: 'pi-times' },
  ERROR: { s: 'danger', icon: 'pi-exclamation-triangle' }, failed: { s: 'danger', icon: 'pi-times' }, 'failed-rolled-back': { s: 'danger', icon: 'pi-undo' },
  timed_out: { s: 'danger', icon: 'pi-hourglass' }, rejected: { s: 'danger', icon: 'pi-times' },
  'blocked-by-baseline': { s: 'danger', icon: 'pi-lock' }, BLOCKED_BY_BASELINE: { s: 'danger', icon: 'pi-lock' },
  APPLIED: { s: 'success', icon: 'pi-check' }, ROLLED_BACK: { s: 'danger', icon: 'pi-undo' },
  PROPOSED_ONLY: { s: 'info', icon: 'pi-lightbulb' }, NO_CHANGES: { s: 'secondary', icon: 'pi-minus' },
  WARN: { s: 'warn', icon: 'pi-exclamation-triangle' }, BLOCKED: { s: 'danger', icon: 'pi-lock' },
  partial_success: { s: 'warn', icon: 'pi-exclamation-circle' }, FINDINGS: { s: 'warn', icon: 'pi-key' },
  high: { s: 'danger', icon: 'pi-arrow-up' }, medium: { s: 'warn', icon: 'pi-minus' }, low: { s: 'info', icon: 'pi-arrow-down' }, info: { s: 'secondary', icon: 'pi-info-circle' },
  'maven-effective': { s: 'success', icon: 'pi-check' }, 'static-local': { s: 'info', icon: 'pi-file' }, 'static-partial': { s: 'warn', icon: 'pi-exclamation-circle' },
};
const LABEL: Record<string, string> = {
  succeeded: 'Correcto', running: 'En curso', queued: 'En cola', failed: 'Fallido', cancelled: 'Cancelado', timed_out: 'Tiempo agotado',
  open: 'Abierto', proposed: 'Propuesto', resolved: 'Resuelto', discarded: 'Descartado', blocked: 'Bloqueado', ready: 'Listo',
  cleared: 'Liberado', pending: 'Pendiente', applied: 'Aplicado', previewed: 'Previsualizado', skipped: 'Omitido', 'no-changes': 'Sin cambios',
  'failed-rolled-back': 'Falló · revertido', rolled_back: 'Descartado (rollback)', approved: 'Aprobado', rejected: 'Rechazado',
  draft: 'Borrador', superseded: 'Reemplazado', validated: 'Validado', retired: 'Retirado', active: 'Activo',
  high: 'Alta', medium: 'Media', low: 'Baja', info: 'Info', NOT_RUN: 'No ejecutado', SKIPPED: 'Omitido',
  'blocked-by-baseline': 'Bloqueado por baseline', BLOCKED_BY_BASELINE: 'Bloqueado por baseline', APPLIED: 'Aplicado',
  ROLLED_BACK: 'Revertido', PROPOSED_ONLY: 'Solo propuesta', NO_CHANGES: 'Sin cambios', WARN: 'Advertencia', BLOCKED: 'Bloqueado', ERROR: 'Error de herramienta',
  partial_success: 'Éxito parcial', FINDINGS: 'Hallazgos',
  'maven-effective': 'Resuelto con Maven', 'static-local': 'Resuelto localmente', 'static-partial': 'Resolución parcial',
};

/** Status never conveyed by color alone: icon + text. */
@Component({
  selector: 'mf-status',
  imports: [TagModule],
  template: `<p-tag [severity]="meta().s" [value]="label()"><i [class]="'pi ' + meta().icon" aria-hidden="true" style="margin-right:.35rem"></i></p-tag>`,
})
export class StatusTag {
  value = input<string | null | undefined>('');
  meta = computed(() => SEVERITY[this.value() ?? ''] ?? { s: 'secondary' as const, icon: 'pi-circle' });
  label = computed(() => LABEL[this.value() ?? ''] ?? this.value() ?? '—');
}

/** Button that, when disabled, states why (visible text, not only a tooltip). */
@Component({
  selector: 'mf-action',
  imports: [ButtonModule],
  template: `
    <span class="action">
      <p-button [label]="label()" [icon]="icon()" [severity]="severity()" [outlined]="outlined()" [loading]="busy()"
                [disabled]="!!disabledReason() || busy()" (onClick)="run.emit()" [attr.aria-describedby]="disabledReason() ? id : null" />
      @if (disabledReason()) { <small class="why" [id]="id"><i class="pi pi-info-circle" aria-hidden="true"></i> {{ disabledReason() }}</small> }
    </span>`,
})
export class ActionButton {
  private static seq = 0;
  id = 'why-' + ActionButton.seq++;
  label = input.required<string>();
  icon = input('');
  severity = input<'primary' | 'secondary' | 'danger' | 'warn' | 'success' | 'contrast' | 'info'>('primary');
  outlined = input(false);
  busy = input(false);
  disabledReason = input<string | null>(null);
  run = output<void>();
}

/** Live, redacted job log over SSE with resume (Last-Event-ID) and optional autoscroll. */
@Component({
  selector: 'mf-job-log',
  imports: [FormsModule, ProgressBarModule, ToggleSwitchModule],
  template: `
    <div class="joblog">
      <div class="joblog-bar">
        <span>Fase: <strong>{{ phase() || '—' }}</strong> · estado <strong>{{ state() || '—' }}</strong></span>
        <label class="inline"><p-toggleswitch [(ngModel)]="autoscroll" inputId="as" /> <span>Autoscroll</span></label>
      </div>
      <p-progressbar [value]="progress()" [showValue]="true" aria-label="Progreso del trabajo" />
      <pre #box class="log" role="log" aria-live="polite">@for (l of lines(); track l.id) {<span [class]="'lv-' + l.level">[{{ l.phase }}] {{ l.message }}</span>
}</pre>
    </div>`,
})
export class JobLog implements OnDestroy {
  private auth = inject(AuthService);
  private api = inject(Api);
  jobId = input.required<string>();
  finished = output<string>();
  lines = signal<{ id: number; level: string; phase: string; message: string }[]>([]);
  phase = signal('');
  state = signal('');
  progress = signal(0);
  autoscroll = signal(true);
  box = viewChild<ElementRef<HTMLPreElement>>('box');
  private abort?: AbortController;

  constructor() {
    effect(() => this.start(this.jobId()));
    effect(() => { this.lines(); const b = this.box()?.nativeElement; if (b && this.autoscroll()) queueMicrotask(() => b.scrollTop = b.scrollHeight); });
  }

  private start(jobId: string) {
    this.abort?.abort();
    this.abort = new AbortController();
    this.lines.set([]);
    streamEvents(`/api/v1/jobs/${jobId}/events`, () => this.auth.token(), m => {
      if (m.event === 'end') { this.state.set(m.data.state); this.progress.set(m.data.state === 'succeeded' ? 100 : this.progress()); this.finished.emit(m.data.state); return; }
      this.lines.update(l => [...l.slice(-1500), { id: m.id ?? 0, level: m.data.level, phase: m.data.phase, message: m.data.message }]);
      this.phase.set(m.data.phase ?? '');
      this.state.set(m.data.state);
      this.progress.set(m.data.progress ?? 0);
    }, this.abort.signal);
  }

  ngOnDestroy() { this.abort?.abort(); }
}

export function short(sha?: string | null) { return sha ? sha.slice(0, 10) : '—'; }

/** improvements/07 traffic light: green = automatic, yellow = needs review, red = blocking/manual. Text, not only color. */
@Component({
  selector: 'mf-light',
  template: `<span class="light" [title]="meta().t"><span class="dot" [style.background]="meta().c" aria-hidden="true"></span>{{ meta().l }}</span>`,
  styles: [`.light{display:inline-flex;align-items:center;gap:.35rem;white-space:nowrap}.dot{width:.7rem;height:.7rem;border-radius:50%;display:inline-block}`],
})
export class TrafficLight {
  classification = input<string | null | undefined>('');
  blocking = input<boolean | null | undefined>(false);
  state = input<string | null | undefined>('');
  meta = computed(() => {
    const c = this.classification(), st = this.state();
    if (this.blocking() || c === 'MANUAL' || st === 'blocked') return { c: '#d32f2f', l: 'Rojo', t: 'Bloqueante o manual' };
    if (c === 'AUTO') return { c: '#2e7d32', l: 'Verde', t: 'Migrable automáticamente' };
    return { c: '#f9a825', l: 'Amarillo', t: 'Automático con revisión' };
  });
}

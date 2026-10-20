import { Component, computed, inject, signal } from '@angular/core';
import { MessageModule } from 'primeng/message';
import { Api } from '../../core/api';
import { PageHeader, StateView, StatusTag } from '../../shared/ui';
import { DataStageStore } from './store';

interface Check { name: string; legacy: string | null; target: string | null; equal: boolean }
interface Table {
  job: string; table: string; stage: string | null; verdict: string; error?: string; excluded: string[]; checks: Check[]; differences: number;
  diff: { onlyLegacy?: string[]; onlyTarget?: string[]; changed?: string[]; truncated?: boolean };
}

/** DataStage → Airflow · Reconciliación: the evidence that Airflow writes the same data DataStage wrote (same input), uploaded
 *  from the client's environment. The platform never connects to the client's databases: it evaluates what the script measured. */
@Component({
  selector: 'mf-ds-reconciliation',
  imports: [MessageModule, PageHeader, StateView, StatusTag],
  template: `
    <mf-page-header title="Reconciliación" crumb="DATASTAGE → AIRFLOW" subtitle="¿Airflow escribe los mismos datos que escribía DataStage?" />
    @if (ds.error(); as e) { <p-message severity="error">{{ e.code }}: {{ e.message }}</p-message> }
    <mf-state [loading]="ds.list.loading()" [error]="ds.list.error()" [hasData]="!!ds.list.data()" [empty]="!ds.current()"
              emptyText="Primero migre el proyecto desde Resumen." (retry)="ds.reload()">
      <ol class="steps">
        <li><strong>Correr los dos lados con la misma entrada.</strong> El job en DataStage escribe en un esquema (legado) y el DAG en
          Airflow en otro (migrado), en un ambiente de pruebas del cliente.
          @if (ds.airflowZip(); as z) { <button type="button" class="link" (click)="api.download(z.id, z.name)"><i class="pi pi-download" aria-hidden="true"></i> Proyecto Airflow</button> }</li>
        <li><strong>Medir en el ambiente del cliente</strong> (las credenciales quedan en sus Connections de Airflow):
          <code>python tests/reconciliation/run_reconciliation.py --conn &lt;connection&gt; --legacy &lt;ESQUEMA_DS&gt; --target &lt;ESQUEMA_AF&gt;</code></li>
        <li><strong>Subir aquí</strong> el <code>reconciliation-result.json</code> que deja el script.
          <label class="upload" [class.disabled]="!!uploadReason()">
            <input type="file" accept=".json,application/json" (change)="upload($event)" [disabled]="!!uploadReason()" />
            <i class="pi pi-upload" aria-hidden="true"></i> {{ ds.busy() ? 'Evaluando…' : 'Subir resultado' }}
          </label>
          @if (uploadReason(); as why) { <small class="muted"><i class="pi pi-info-circle" aria-hidden="true"></i> {{ why }}</small> }
          @if (parseError(); as pe) { <p-message severity="error">{{ pe }}</p-message> }</li>
      </ol>

      @if (rec(); as r) {
        <section class="summary">
          <div class="big"><mf-status [value]="r.status" /> <span>{{ verdictText() }}</span></div>
          <div class="kpis">
            <div><strong>{{ r.counts.VERIFIED }}</strong><span>verificados</span></div>
            <div [class.bad]="r.counts.DIFFERENT"><strong>{{ r.counts.DIFFERENT }}</strong><span>con diferencias</span></div>
            <div [class.bad]="r.counts.ERROR"><strong>{{ r.counts.ERROR }}</strong><span>no se pudieron medir</span></div>
            <div><strong>{{ r.counts.NOT_RECONCILED }}</strong><span>sin tabla Oracle o sin medir</span></div>
          </div>
          <p class="muted small">Medido el {{ (r.executedAt ?? '').slice(0, 16).replace('T', ' ') }} · legado <span class="mono">{{ r.legacy?.schema }}</span>
            · migrado <span class="mono">{{ r.target?.schema }}</span> · subido por {{ r.uploadedBy }} el {{ r.uploadedAt.slice(0, 10) }}</p>
        </section>

        @for (t of tables(); track t.job + t.table) {
          <details class="table" [open]="t.verdict !== 'EQUAL'">
            <summary><mf-status [value]="t.verdict" /> <span class="mono">{{ t.job }}</span> › <span class="mono">{{ t.table }}</span>
              @if (t.differences) { <span class="count bad">{{ t.differences }} diferencia(s)</span> }</summary>
            @if (t.error) { <p class="err mono small">{{ t.error }}</p> }
            @if (t.checks.length) {
              <table class="checks">
                <thead><tr><th>Chequeo</th><th>DataStage (legado)</th><th>Airflow (migrado)</th></tr></thead>
                <tbody>@for (c of t.checks; track c.name) {
                  <tr [class.diff]="!c.equal"><td>{{ checkLabel(c.name) }}</td><td class="mono">{{ c.legacy ?? '(nulo)' }}</td><td class="mono">{{ c.target ?? '(nulo)' }}</td></tr>
                }</tbody>
              </table>
            }
            @for (k of diffKinds; track k.key) {
              @if (t.diff[k.key]?.length) {
                <p class="small"><strong>{{ k.label }}</strong> (clave{{ t.diff.truncated ? ', muestra' : '' }}):
                  @for (v of t.diff[k.key]; track v) { <span class="key mono">{{ v }}</span> }</p>
              }
            }
            @if (t.excluded.length) { <p class="muted small">No comparadas (se llenan con la hora de ejecución): {{ t.excluded.join(', ') }}</p> }
          </details>
        }
        @if (history().length > 1) {
          <details class="small"><summary>Mediciones anteriores ({{ history().length - 1 }})</summary>
            <ul>@for (h of history().slice(1); track h.at) { <li>{{ h.at.slice(0, 16).replace('T', ' ') }} · {{ h.by }} · <mf-status [value]="h.status" />
              {{ h.counts.VERIFIED }} verificados, {{ h.counts.DIFFERENT }} con diferencias</li> }</ul>
          </details>
        }
      } @else {
        <p class="empty">Todavía no hay una reconciliación: el proyecto está migrado y validado técnicamente, pero falta probar que los datos coinciden.</p>
      }
    </mf-state>`,
  styles: [`.steps{background:#fff;border:1px solid var(--mf-line);border-radius:12px;padding:14px 18px 14px 36px;margin:0 0 14px}
    .steps li{margin:6px 0;line-height:1.5}.steps code{display:block;margin:4px 0;padding:6px 8px;background:#f3f6f9;border-radius:6px;font-size:12px;overflow-x:auto}
    .upload{display:inline-flex;gap:6px;align-items:center;margin:6px 10px 0 0;padding:6px 14px;border-radius:8px;background:#0a7ea4;color:#fff;cursor:pointer;font-weight:600}
    .upload input{display:none}.upload.disabled{background:#9aa8b4;cursor:not-allowed}
    .link{background:none;border:0;color:#0a7ea4;cursor:pointer;padding:0 4px;font:inherit}
    .summary{background:#fff;border:1px solid var(--mf-line);border-radius:12px;padding:14px 18px;margin-bottom:12px}
    .big{display:flex;gap:10px;align-items:center;font-size:15px}.kpis{display:flex;gap:28px;margin:12px 0 6px;flex-wrap:wrap}
    .kpis div{display:flex;flex-direction:column}.kpis strong{font-size:22px}.kpis span{font-size:12px;color:var(--mf-muted)}.kpis .bad strong{color:#b42318}
    .table{background:#fff;border:1px solid var(--mf-line);border-radius:12px;padding:10px 16px;margin-bottom:10px}
    .table summary{cursor:pointer;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
    .count{font-size:12px;font-weight:600;border-radius:999px;padding:1px 9px}.count.bad{background:#fee4e2;color:#b42318}
    .checks{width:100%;border-collapse:collapse;margin:10px 0;font-size:13px}.checks th,.checks td{text-align:left;padding:5px 8px;border-top:1px solid var(--mf-line)}
    .checks th{font-size:12px;color:var(--mf-muted);font-weight:600}.checks tr.diff td{background:#fef3f2;color:#b42318;font-weight:600}
    .key{display:inline-block;background:#f3f6f9;border-radius:6px;padding:0 6px;margin:2px}.err{color:#b42318}`],
})
export class DsReconciliation {
  ds = inject(DataStageStore);
  api = inject(Api);
  parseError = signal<string | null>(null);
  readonly diffKinds = [{ key: 'changed', label: 'Filas con valores distintos' }, { key: 'onlyLegacy', label: 'Solo en DataStage' },
    { key: 'onlyTarget', label: 'Solo en Airflow' }] as const;
  rec = computed(() => this.ds.reconciliation());
  tables = computed<Table[]>(() => [...(this.rec()?.tables ?? [])]
    .sort((a: Table, b: Table) => Number(a.verdict === 'EQUAL') - Number(b.verdict === 'EQUAL')));
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  history = computed<any[]>(() => (this.ds.current()?.reconciliation as any)?.history ?? []);
  uploadReason = computed(() => {
    if (!this.ds.ctx.can('execute')) return `Su rol (${this.ds.ctx.role()}) no permite registrar evidencia`;
    if (!['succeeded', 'partial_success'].includes(this.ds.current()?.state ?? '')) return 'La migración aún no termina';
    return this.ds.busy() ? 'Evaluando…' : null;
  });
  verdictText = computed(() => {
    const r = this.rec();
    if (!r) return '';
    if (r.status === 'VERIFIED') return 'Los datos de Airflow coinciden con los de DataStage en todo lo medido.';
    if (r.status === 'DIFFERENT') return 'Hay diferencias: revise las tablas marcadas antes de dar el job por migrado.';
    return 'Algunas tablas no se pudieron medir (permisos, tabla inexistente…).';
  });

  checkLabel(name: string) {
    const [k, col] = name.split(':');
    const label: Record<string, string> = { row_count: 'Filas', business_key_count: 'Claves distintas', duplicate_count: 'Claves duplicadas',
      checksum: 'Checksum de las filas', null_count: 'Nulos en', sum: 'Suma de', min: 'Mínimo de', max: 'Máximo de' };
    return col ? `${label[k] ?? k} ${col}` : label[k] ?? k;
  }

  async upload(ev: Event) {
    const input = ev.target as HTMLInputElement;
    const file = input.files?.[0];
    input.value = '';
    this.parseError.set(null);
    if (!file) return;
    if (file.size > 5_000_000) { this.parseError.set('El archivo supera 5 MB.'); return; }
    let data: unknown;
    try { data = JSON.parse(await file.text()); }
    catch { this.parseError.set('No es un JSON válido: suba el reconciliation-result.json que genera el script.'); return; }
    await this.ds.uploadReconciliation(data);
  }
}

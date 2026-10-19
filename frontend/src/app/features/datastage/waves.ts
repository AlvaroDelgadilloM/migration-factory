import { Component, computed, inject } from '@angular/core';
import { PageHeader, StateView } from '../../shared/ui';
import { DataStageStore } from './store';

/** DataStage → Airflow · Oleadas: the migration order drawn as columns (a wave only depends on the ones to its left). */
@Component({
  selector: 'mf-ds-waves',
  imports: [PageHeader, StateView],
  template: `
    <mf-page-header title="Oleadas" crumb="DATASTAGE → AIRFLOW" [subtitle]="ds.ctx.project()?.name" />
    <p class="muted">Orden de migración calculado de las dependencias (secuencia → job, encadenamiento y archivos o tablas que un job produce y
      otro consume). Los jobs de una misma oleada son independientes entre sí y se pueden migrar en paralelo.</p>
    <mf-state [loading]="ds.list.loading()" [error]="ds.list.error()" [hasData]="!!ds.list.data()" [empty]="!waves().length"
              emptyText="Sin resultados todavía: migre el proyecto desde Resumen." (retry)="ds.reload()">
      <div class="legend small">
        <span><i class="dot st-AUTO_MIGRATED"></i> Automático</span><span><i class="dot st-SEMIAUTOMATIC"></i> Revisar</span>
        <span><i class="dot st-MANUAL_REVIEW"></i> Trabajo manual</span><span><i class="seq">S</i> Secuencia</span>
      </div>
      <div class="board" role="list" aria-label="Oleadas de migración">
        @for (w of waves(); track w.wave) {
          <section class="wave" role="listitem">
            <h3>Oleada {{ w.wave + 1 }} <span class="muted small">· {{ w.jobs.length }}</span></h3>
            @for (j of w.jobs; track j.name) {
              <div class="job" [class]="'job st-' + j.state" [title]="j.name + ' · ' + j.state">
                @if (j.jobType === 'SEQUENCE') { <i class="seq" aria-label="secuencia">S</i> }
                <span class="mono">{{ j.name }}</span>
              </div>
            }
          </section>
        }
      </div>
      @if (ds.summary()?.droppedEdges?.length; as n) {
        <details class="small"><summary>{{ n }} dependencia(s) de datos descartadas porque contradecían el orden de una secuencia</summary>
          <ul>@for (d of ds.summary().droppedEdges; track $index) { <li><span class="mono">{{ d.from }}</span> → <span class="mono">{{ d.to }}</span> ({{ d.resource }})</li> }</ul>
        </details>
      }
    </mf-state>`,
  styles: [`.board{display:flex;gap:14px;overflow-x:auto;padding-bottom:12px;align-items:flex-start}
    .wave{flex:0 0 17rem;background:#fff;border:1px solid var(--mf-line);border-radius:12px;padding:10px 12px}
    .wave h3{margin:2px 0 8px;font-size:14px}.job{display:flex;gap:6px;align-items:center;padding:6px 8px;margin:5px 0;border-radius:8px;
    border-left:4px solid #9aa8b4;background:#f7f9fb;font-size:12px;overflow:hidden}.job .mono{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    .st-AUTO_MIGRATED{border-left-color:#12b76a}.st-SEMIAUTOMATIC{border-left-color:#0a7ea4}.st-MANUAL_REVIEW{border-left-color:#f79009}
    .st-BLOCKED,.st-FAILED{border-left-color:#d92d20}.seq{font-style:normal;font-weight:700;font-size:10px;background:#10283c;color:#fff;border-radius:4px;padding:0 4px}
    .legend{display:flex;gap:16px;margin:0 0 10px}.legend span{display:inline-flex;gap:6px;align-items:center}
    .dot{width:10px;height:10px;border-radius:2px;display:inline-block;border-left:10px solid}`],
})
export class DsWaves {
  ds = inject(DataStageStore);
  waves = computed(() => {
    const byName = new Map(this.ds.jobs().map(j => [j.name, j]));
    return ((this.ds.summary()?.waves ?? []) as { wave: number; jobs: string[] }[])
      .map(w => ({ wave: w.wave, jobs: w.jobs.map(n => byName.get(n) ?? { name: n, state: '', jobType: '' }) }));
  });
}

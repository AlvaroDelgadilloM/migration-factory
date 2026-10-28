import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { InputTextModule } from 'primeng/inputtext';
import { SelectButtonModule } from 'primeng/selectbutton';
import { TableModule } from 'primeng/table';
import { OdooModule } from '../../core/models';
import { PageHeader, StateView } from '../../shared/ui';
import { OdooStore } from './store';

const VERSIONS = ['13.0', '14.0', '15.0', '16.0', '17.0', '18.0', '19.0'];
const ORIGIN: Record<string, string> = { CORE: 'Núcleo Odoo', OCA: 'OCA', THIRD_PARTY: 'Terceros / a la medida' };
const OUTCOME: Record<string, string> = { OK: 'Migra', WARN: 'Revisar', BLOCK: 'Bloquea' };

/** Odoo 12 -> 19 · Módulos: every installed module, where it comes from, whether it exists in each version up to 19, and the
 *  architect's decision for the ones that block (third party / custom, or OCA not ported). */
@Component({
  selector: 'mf-odoo-modules',
  imports: [FormsModule, InputTextModule, SelectButtonModule, TableModule, PageHeader, StateView],
  styles: [`
    .tag { font-size: 12px; padding: 2px 8px; border-radius: 10px; white-space: nowrap; } .t-OK { background: #e7f6ec; color: #067647; }
    .t-WARN { background: #fff4e5; color: #b54708; } .t-BLOCK { background: #fdecea; color: #b42318; }
    .vers { display: flex; gap: 3px; flex-wrap: wrap; } .vers span { font: 11px ui-monospace, Menlo, monospace; padding: 1px 4px; border-radius: 4px; }
    .yes { background: #e7f6ec; color: #067647; } .no { background: #fdecea; color: #b42318; }
    .decide { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 6px; } .decide input { flex: 1 1 14rem; min-width: 0; }
    .toolbar { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin: 0 0 10px; } .small { font-size: 12.5px; }
    .muted { color: var(--mf-muted); } button.link { background: none; border: 0; color: var(--mf-teal); cursor: pointer; padding: 0; }`],
  template: `
    <mf-page-header title="Módulos" crumb="ODOO 12 → 19" subtitle="Cada módulo instalado: de dónde viene y si existe en cada versión hasta 19." />
    @if (os.error(); as e) { <p class="err">{{ e.code }}: {{ e.message }}</p> }
    <mf-state [loading]="os.list.loading()" [error]="os.list.error()" [hasData]="!!os.analysis()" [empty]="!os.analysis()"
              emptyText="Primero analice la base en Resumen." (retry)="os.reload()">
      <p class="small muted">Núcleo: según la cobertura publicada de OpenUpgrade por salto. OCA: si el módulo existe en la rama de cada versión
        (su código hace falta en todos los saltos para migrar sus datos). Terceros o a la medida: no hay versión 19 conocida.
        Un módulo que bloquea solo deja migrar si un arquitecto decide desinstalarlo, con motivo (sus datos no se migran).</p>
      <div class="toolbar">
        <p-selectbutton [options]="filters" optionLabel="label" optionValue="value" [ngModel]="filter()" (ngModelChange)="filter.set($event)" aria-label="Filtrar módulos" />
        <input pInputText placeholder="Buscar módulo…" [ngModel]="q()" (ngModelChange)="q.set($event)" aria-label="Buscar módulo" />
      </div>
      <p-table [value]="rows()" [paginator]="rows().length > 50" [rows]="50" [tableStyle]="{ 'min-width': '52rem' }">
        <ng-template #header><tr><th scope="col">Módulo</th><th scope="col">Origen</th><th scope="col">Resultado</th><th scope="col">Versiones 13 → 19</th><th scope="col">Detalle y decisión</th></tr></ng-template>
        <ng-template #body let-m>
          <tr>
            <td><code>{{ m.name }}</code><br /><span class="small muted">{{ m.author }}</span></td>
            <td class="small">{{ origin(m.origin) }}@if (m.repository) { <br /><span class="muted">{{ m.repository }}</span> }</td>
            <td><span [class]="'tag t-' + m.outcome">{{ outcome(m.outcome) }}</span></td>
            <td>
              @if (m.availability) {
                <span class="vers">@for (v of versions; track v) { <span [class]="m.availability[v] ? 'yes' : 'no'" [title]="v + (m.availability[v] ? ': disponible' : ': no existe')">{{ v.slice(0, 2) }}</span> }</span>
              } @else if (m.path) {
                <span class="vers">@for (p of m.path; track p.hop) { <span [class]="p.status === 'DONE' || p.status === 'NOTHING_TO_DO' ? 'yes' : 'no'" [title]="p.hop + ': ' + p.status + (p.note ? ' · ' + p.note : '')">{{ p.hop.slice(-4, -2) }}</span> }</span>
                @if (m.final && m.final !== m.name) { <span class="small muted">en 19: {{ m.final }}</span> }
              } @else { — }
            </td>
            <td class="small">{{ m.reason }}
              @if (m.outcome === 'BLOCK') {
                @if (decision(m.name); as d) {
                  <div><strong>Se desinstala antes de migrar</strong> · {{ d.reason }} · {{ d.by }}
                    @if (os.ctx.can('plan')) { <button class="link" (click)="os.decide(m.name, 'pending', '')">deshacer</button> }</div>
                } @else if (os.ctx.can('plan')) {
                  <div class="decide"><input pInputText [(ngModel)]="reasons[m.name]" [attr.aria-label]="'Motivo para desinstalar ' + m.name"
                                             placeholder="Motivo (p. ej. ya no se usa; sus datos se exportaron)" />
                    <button class="p-button p-button-sm p-button-danger" [disabled]="(reasons[m.name] ?? '').trim().length < 10"
                            (click)="os.decide(m.name, 'uninstall', reasons[m.name])">Desinstalar antes de migrar</button></div>
                } @else { <div class="muted">Pendiente: decide un arquitecto.</div> }
              }
            </td>
          </tr>
        </ng-template>
      </p-table>
    </mf-state>`,
})
export class OdooModules {
  os = inject(OdooStore);
  versions = VERSIONS;
  reasons: Record<string, string> = {};
  filter = signal('PROBLEMS');
  q = signal('');
  filters = [{ label: 'Bloquean o revisar', value: 'PROBLEMS' }, { label: 'Terceros y OCA', value: 'EXTERNAL' }, { label: 'Todos', value: 'ALL' }];
  rows = computed<OdooModule[]>(() => {
    const q = this.q().trim().toLowerCase(), f = this.filter();
    const order = { BLOCK: 0, WARN: 1, OK: 2 } as Record<string, number>;
    return (this.os.analysis()?.modules ?? [])
      .filter(m => f === 'ALL' || (f === 'PROBLEMS' ? m.outcome !== 'OK' : m.origin !== 'CORE'))
      .filter(m => !q || m.name.includes(q) || (m.author ?? '').toLowerCase().includes(q))
      .sort((a, b) => order[a.outcome] - order[b.outcome] || a.name.localeCompare(b.name));
  });
  origin(o: string) { return ORIGIN[o] ?? o; }
  outcome(o: string) { return OUTCOME[o] ?? o; }
  decision(name: string) { return this.os.analysis()?.decisions?.[name] ?? null; }
}

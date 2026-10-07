import { Component, effect, inject, input, signal } from '@angular/core';
import { Api } from '../../core/api';
import { ChangeEntry } from '../../core/models';
import { Loader } from '../../shared/state';
import { StateView, TrafficLight } from '../../shared/ui';

/** improvements/14: per applied step, rule, lines, before/after, confidence level and individual rollback patch. */
@Component({
  selector: 'mf-changes',
  imports: [StateView, TrafficLight],
  template: `
    <mf-state [loading]="list.loading()" [error]="list.error()" [hasData]="!!list.data()" [empty]="list.data()?.length === 0"
              emptyText="Sin cambios registrados (preview, o ejecución anterior al registro de cambios)." (retry)="list.reload()">
      <p class="muted">La confianza es un nivel, no un porcentaje. Cada paso tiene su parche; se revierte con el comando indicado.</p>
      @for (c of list.data() ?? []; track c.step) {
        <details class="change">
          <summary>
            <mf-light [classification]="c.confidence" /> <strong>{{ c.step }}</strong> · {{ c.confidence }} · {{ c.files.length }} archivo(s)
            · reglas: <span class="mono small">{{ c.rules.join(', ') || '—' }}</span>
          </summary>
          <p><small>{{ c.confidenceMeaning }}</small></p>
          <ul class="small">@for (r of c.rationale; track $index) { <li>{{ r }}</li> }</ul>
          <p class="small">Snapshot <code>{{ (c.snapshotBefore ?? '').slice(0, 10) }}</code> → <code>{{ (c.snapshotAfter ?? '').slice(0, 10) }}</code>
            · rollback: <code>{{ c.rollback.command }}</code>
            @if (c.patchArtifactId) { · <button type="button" class="link" (click)="api.download(c.patchArtifactId!, c.patch)"><i class="pi pi-download"></i> {{ c.patch }}</button> }</p>
          @for (l of c.changes.slice(0, shown()); track $index) {
            <div class="hunk"><div class="mono small"><strong>{{ l.file }}</strong> (líneas {{ l.lines }})</div>
              <pre class="diff">@for (b of lines(l.before); track $index) {<span class="del">- {{ b }}</span>
}@for (a of lines(l.after); track $index) {<span class="add">+ {{ a }}</span>
}</pre></div>
          }
          @if (c.changes.length > shown()) { <button type="button" class="link" (click)="shown.set(shown() + 50)">Ver más ({{ c.changes.length - shown() }})</button> }
        </details>
      }
    </mf-state>`,
  styles: [`.change{border:1px solid var(--p-content-border-color,#ddd);border-radius:6px;padding:.5rem .75rem;margin:.5rem 0}
    summary{cursor:pointer}.diff{margin:.25rem 0 .75rem;overflow-x:auto;font-size:.8rem}.del{color:#b71c1c;display:block}.add{color:#1b5e20;display:block}`],
})
export class ChangesPanel {
  executionId = input.required<string>();
  api = inject(Api);
  list = new Loader<ChangeEntry[]>();
  shown = signal(50);
  constructor() { effect(() => { const id = this.executionId(); this.list.load(() => this.api.changes(id)); }); }
  lines(t: string) { return t ? t.split('\n') : []; }
}

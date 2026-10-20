import { Component, computed, effect, input, model } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { SelectModule } from 'primeng/select';
import { TargetVersions } from '../core/models';

/**
 * Java and Spring of a migration target (GET /target-versions). Only validated combinations can be chosen: a Java the
 * target was not validated with is shown disabled with its reason, and the Spring list follows the chosen Java.
 */
@Component({
  selector: 'mf-version-picker',
  imports: [FormsModule, SelectModule],
  template: `
    <span class="vp"><label [for]="id + 'j'">Java</label>
      <p-select [inputId]="id + 'j'" [options]="javaOptions()" optionLabel="label" optionValue="value" optionDisabled="disabled"
                [ngModel]="java()" (ngModelChange)="java.set($event)" [disabled]="!entry()" [style]="{ minWidth: '13rem' }" /></span>
    <span class="vp"><label [for]="id + 's'">Spring</label>
      <p-select [inputId]="id + 's'" [options]="springOptions()" optionLabel="label" optionValue="key"
                [ngModel]="spring()" (ngModelChange)="spring.set($event)" [disabled]="!entry()" [style]="{ minWidth: '20rem' }"
                placeholder="Sin versión compatible" /></span>
    @if (hint(); as h) { <small class="muted vp-hint">{{ h }}</small> }`,
  styles: [`.vp{display:inline-flex;flex-direction:column;gap:4px;margin-right:12px} .vp label{font-size:12px;color:var(--mf-muted)}
            .vp-hint{display:block;margin-top:4px}`],
})
export class VersionPicker {
  private static seq = 0;
  id = 'vp' + VersionPicker.seq++;
  entry = input<TargetVersions | null>(null);
  java = model('21');
  spring = model<string | null>(null);

  javaOptions = computed(() => (this.entry()?.java ?? []).map(j => ({
    value: j.version, disabled: !j.validated, label: j.validated ? `Java ${j.version}` : `Java ${j.version} · pendiente de validar`,
  })));
  springOptions = computed(() => (this.entry()?.spring ?? []).filter(s => s.java.includes(this.java())));
  hint = computed(() => {
    const blocked = (this.entry()?.java ?? []).filter(j => !j.validated);
    return blocked.length ? `${blocked.map(j => 'Java ' + j.version).join(', ')}: ${blocked[0].reason}.` : '';
  });

  constructor() {
    // keep the pair valid: a Java the target does not accept falls back to its first validated one, and the Spring line
    // follows the Java (the only compatible one is selected by itself)
    effect(() => {
      const e = this.entry();
      if (!e) return;
      if (!e.java.some(j => j.version === this.java() && j.validated)) {
        const first = e.java.find(j => j.validated);
        if (first) this.java.set(first.version);
      }
      const springs = this.springOptions();
      if (!springs.some(s => s.key === this.spring())) this.spring.set(springs[0]?.key ?? null);
    });
  }
}

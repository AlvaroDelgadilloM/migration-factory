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

type Prof = { id: string; key: string; version: number; runtime: string; dependencyMapping?: Record<string, unknown> | null };

/** Every Spring Boot (or Quarkus) profile of the catalog as one destination: a Java is offered when ANY profile of that runtime
 *  validated it, and the Spring lines list each profile with the Java it accepts (Java 25 → Spring Boot 4.1 + Camel 4.22).
 *  The external-Tomcat WAR variant is its own destination and stays out. */
export function runtimeEntry(entries: TargetVersions[] | null | undefined, runtime: string): TargetVersions | null {
  const same = (entries ?? []).filter(e => e.runtime === runtime && !/^Tomcat/.test(e.label ?? ''));
  if (same.length <= 1) return same[0] ?? (entries ?? []).find(e => e.runtime === runtime) ?? null;
  const java = same[0].java.map(j => {
    const ok = same.find(e => e.java.some(x => x.version === j.version && x.validated));
    return ok ? { ...j, validated: true, reason: null } : j;
  });
  return { ...same[0], label: same.map(e => e.label).join(' / '), java, spring: same.flatMap(e => e.spring) };
}

/** Profile for a chosen destination: the Spring line picked (profile key@version), else the first profile of the runtime that
 *  validated the chosen Java, else the runtime's first profile. */
export function profileFor(ps: Prof[] | null | undefined, d: { runtime: string; java: string; spring: string | null }): string | null {
  const same = (ps ?? []).filter(p => p.runtime === d.runtime && !p.dependencyMapping?.['deployment']);
  const byKey = d.spring ? same.find(p => `${p.key}@${p.version}` === d.spring) : undefined;
  const byJava = same.find(p => ((p.dependencyMapping?.['javaVersions'] as string[] | undefined) ?? ['17', '21']).includes(d.java));
  return (byKey ?? byJava ?? same[0])?.id ?? null;
}

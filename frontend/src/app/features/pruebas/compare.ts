import { Component, computed, input } from '@angular/core';
import { HttpAnswer } from '../../core/models';

export interface Side extends HttpAnswer { label: string; url?: string }

/** Formats JSON and XML so both answers line up; the comparison itself was made on the raw text. */
export function pretty(body: string, type: string): string[] {
  const text = body ?? '';
  try {
    const t = text.trimStart();
    if (type.includes('json') || t.startsWith('{') || t.startsWith('[')) return JSON.stringify(JSON.parse(text), null, 2).split('\n');
    if (t.startsWith('<') && !type.includes('html')) return prettyXml(text);
  } catch { /* not well formed: as it came */ }
  return text ? text.split(/\r?\n/) : ['(sin cuerpo)'];
}

function prettyXml(xml: string): string[] {
  const doc = new DOMParser().parseFromString(xml, 'application/xml');
  if (doc.getElementsByTagName('parsererror').length) return xml.split(/\r?\n/);
  const out: string[] = [];
  const walk = (n: Node, depth: number) => {
    const pad = '  '.repeat(depth);
    if (n.nodeType === Node.TEXT_NODE) { const t = n.textContent?.trim(); if (t) out.push(pad + t); return; }
    if (n.nodeType !== Node.ELEMENT_NODE) return;
    const el = n as Element;
    const attrs = Array.from(el.attributes).map(a => ` ${a.name}="${a.value}"`).join('');
    const kids = Array.from(el.childNodes).filter(c => c.nodeType === Node.ELEMENT_NODE || c.textContent?.trim());
    if (!kids.length) { out.push(`${pad}<${el.tagName}${attrs}/>`); return; }
    if (kids.length === 1 && kids[0].nodeType === Node.TEXT_NODE) { out.push(`${pad}<${el.tagName}${attrs}>${kids[0].textContent?.trim()}</${el.tagName}>`); return; }
    out.push(`${pad}<${el.tagName}${attrs}>`);
    kids.forEach(k => walk(k, depth + 1));
    out.push(`${pad}</${el.tagName}>`);
  };
  walk(doc.documentElement, 0);
  return out;
}

/** Lines of each side that are not in the longest common subsequence (what changed). Large bodies: line by line. */
export function changed(a: string[], b: string[]): [Set<number>, Set<number>] {
  if (a.length * b.length > 4_000_000) {
    const da = new Set<number>(), db = new Set<number>();
    for (let i = 0; i < Math.max(a.length, b.length); i++) if (a[i] !== b[i]) { if (i < a.length) da.add(i); if (i < b.length) db.add(i); }
    return [da, db];
  }
  const m = a.length, n = b.length;
  const L = Array.from({ length: m + 1 }, () => new Uint32Array(n + 1));
  for (let i = m - 1; i >= 0; i--) for (let j = n - 1; j >= 0; j--) L[i][j] = a[i] === b[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  const keepA = new Set<number>(), keepB = new Set<number>();
  for (let i = 0, j = 0; i < m && j < n;) {
    if (a[i] === b[j]) { keepA.add(i++); keepB.add(j++); } else if (L[i + 1][j] >= L[i][j + 1]) i++; else j++;
  }
  return [new Set(a.map((_, i) => i).filter(i => !keepA.has(i))), new Set(b.map((_, i) => i).filter(i => !keepB.has(i)))];
}

/** One or two HTTP answers side by side; with two, the lines that differ are highlighted. */
@Component({
  selector: 'mf-compare',
  styles: [`
    .sides { display: grid; grid-template-columns: repeat(auto-fit, minmax(22rem, 1fr)); gap: 14px; }
    .side { border: 1px solid var(--mf-line); border-radius: 8px; background: #fff; min-width: 0; }
    .side header { padding: 8px 12px; background: #f1f4f8; border-bottom: 1px solid var(--mf-line); }
    .side header b { display: block; } .meta { padding: 6px 12px; border-bottom: 1px solid var(--mf-line); font-size: 13px; }
    pre { margin: 0; padding: 6px 0; max-height: 34rem; overflow: auto; font: 12.5px/1.45 ui-monospace, Menlo, monospace; white-space: pre-wrap; word-break: break-all; }
    pre span { display: block; padding: 0 12px; min-height: 1.45em; } pre span.d { background: #ffe9a8; }
    .bad { color: #b42318; }`],
  template: `
    <div class="sides">
      @for (s of view(); track $index) {
        <section class="side" [attr.aria-label]="'Respuesta de ' + s.label">
          <header><b>{{ s.label }}</b>@if (s.url) { <small class="muted mono">{{ s.url }}</small> }</header>
          <div class="meta">
            @if (s.status === 0) { <span class="bad">Sin respuesta</span> }
            @else { HTTP <b>{{ s.status }}</b> · Content-Type <code>{{ s.type || '(ninguno)' }}</code> }
            @if (s.truncated) { <span class="muted"> · recortado (completo en la evidencia)</span> }
          </div>
          <pre>@for (l of s.lines; track $index) {<span [class.d]="s.diff.has($index)">{{ l }}</span>}</pre>
        </section>
      }
    </div>
    @if (view().length === 2) { <p class="muted small">Las líneas resaltadas son las que no coinciden.</p> }`,
})
export class CompareView {
  sides = input.required<Side[]>();
  view = computed(() => {
    const s = this.sides();
    const lines = s.map(x => pretty(x.body, x.type));
    const diff = lines.length === 2 ? changed(lines[0], lines[1]) : [new Set<number>()];
    return s.map((x, i) => ({ ...x, lines: lines[i], diff: diff[i] }));
  });
}

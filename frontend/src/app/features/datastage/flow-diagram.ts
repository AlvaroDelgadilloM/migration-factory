import { Component, computed, input } from '@angular/core';

export interface SeqFlow {
  nodes: { name: string; type: string; task: string; operator: string; job: string | null }[];
  edges: { from: string; to: string; trigger: string | null; expression: string | null }[];
}

const W = 236, H = 92, GX = 56, GY = 16, PAD = 12;
const TYPE: Record<string, { label: string; color: string }> = {
  CJobActivity: { label: 'Job', color: '#0b8a8f' }, CCondition: { label: 'Condición', color: '#b54708' },
  CTerminatorActivity: { label: 'Terminador', color: '#b42318' }, CExceptionHandler: { label: 'Excepción', color: '#b42318' },
  CExecCommandActivity: { label: 'Comando', color: '#5b21b6' }, CUserVarsActivity: { label: 'Variables', color: '#1d4f91' },
  CSequencer: { label: 'Sincronizador', color: '#475569' },
};

/** DataStage sequence next to the Airflow DAG generated from it: each box is one DataStage activity (top) and the Airflow task
 *  it became (bottom); arrows are the sequence links (custom trigger = condition, failure = exception handler). */
@Component({
  selector: 'mf-flow-diagram',
  template: `
    <div class="legend small">
      <span><i class="ln"></i> sin condición</span><span><i class="ln cond"></i> condición</span>
      @if (handlers().length) { <span><i class="pi pi-bolt fail-i"></i> {{ handlers().join(', ') }}: se activa si falla cualquier job</span> }
      <span class="muted">Arriba: DataStage · abajo: tarea de Airflow</span>
    </div>
    <div class="scroll">
      <svg [attr.width]="layout().w" [attr.height]="layout().h" [attr.viewBox]="'0 0 ' + layout().w + ' ' + layout().h" role="img"
           [attr.aria-label]="'Secuencia de ' + flow().nodes.length + ' actividades y su DAG de Airflow'">
        <defs>
          <marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M0,0 L10,5 L0,10 z" fill="#94a3b8" /></marker>
          <marker id="ahc" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M0,0 L10,5 L0,10 z" fill="#d97706" /></marker>
          <marker id="ahf" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M0,0 L10,5 L0,10 z" fill="#dc2626" /></marker>
        </defs>
        @for (e of layout().edges; track $index) {
          <path [attr.d]="e.d" fill="none" [attr.stroke]="e.color" stroke-width="1.6" [attr.stroke-dasharray]="e.dash"
                [attr.marker-end]="'url(#' + e.marker + ')'"><title>{{ e.title }}</title></path>
        }
        @for (n of layout().nodes; track n.name) {
          <g [attr.transform]="'translate(' + n.x + ',' + n.y + ')'">
            <title>{{ n.name }} ({{ n.typeLabel }}){{ n.job ? ' → ' + n.job : '' }} · Airflow: {{ n.task }} ({{ n.operator }})</title>
            <rect [attr.width]="W" [attr.height]="H" rx="10" fill="#fff" stroke="#dbe4ec" />
            <rect [attr.width]="5" [attr.height]="H" rx="2" [attr.fill]="n.color" />
            <text x="14" y="20" class="t1">{{ clip(n.name, 27) }}</text>
            <text x="14" y="36" class="t2" [attr.fill]="n.color">{{ n.typeLabel }}</text>
            <line x1="12" [attr.x2]="W - 10" y1="45" y2="45" stroke="#eef3f7" />
            <text x="14" y="62" class="t3">→ {{ clip(n.task, 28) }}</text>
            <text x="14" y="80" class="t4">{{ clip(n.operator, 34) }}</text>
          </g>
        }
      </svg>
    </div>`,
  styles: [`.scroll{overflow:auto;border:1px solid var(--mf-line);border-radius:12px;background:#f8fafc;padding:4px}
    svg{display:block}.t1{font:600 12.5px ui-monospace,Menlo,monospace;fill:#16293a}.t2{font:600 11.5px system-ui}
    .t3{font:11.5px ui-monospace,Menlo,monospace;fill:#16293a}.t4{font:11px system-ui;fill:#536679}
    .fail-i{color:#dc2626}
    .legend{display:flex;gap:16px;flex-wrap:wrap;margin:0 0 8px;align-items:center}.legend span{display:inline-flex;gap:6px;align-items:center}
    .ln{display:inline-block;width:22px;border-top:2px solid #94a3b8}.ln.cond{border-top:2px dashed #d97706}.ln.fail{border-top:2px dashed #dc2626}`],
})
export class FlowDiagram {
  flow = input.required<SeqFlow>();
  W = W;
  H = H;
  /** exception handlers fire on any job failure: shown as a note instead of one arrow per job crossing the diagram */
  handlers = computed(() => this.flow().nodes.filter(n => n.type === 'CExceptionHandler').map(n => n.name));
  clip(s: string, n: number) { return s.length > n ? s.slice(0, n - 1) + '…' : s; }

  layout = computed(() => {
    const f = this.flow();
    const names = f.nodes.map(n => n.name);
    const incoming = new Map(names.map(n => [n, [] as string[]]));
    const drawn = f.edges.filter(e => e.trigger !== 'failure');
    for (const e of drawn) incoming.get(e.to)?.push(e.from);
    // level = longest path from a source (the sequence is acyclic; a guard keeps a malformed export from looping)
    const level = new Map<string, number>();
    const depth = (n: string, seen: Set<string>): number => {
      if (level.has(n)) return level.get(n)!;
      if (seen.has(n)) return 0;
      seen.add(n);
      const v = Math.max(-1, ...(incoming.get(n) ?? []).map(p => depth(p, seen))) + 1;
      level.set(n, v);
      return v;
    };
    names.forEach(n => depth(n, new Set()));
    const cols = new Map<number, string[]>();
    for (const n of names) cols.set(level.get(n)!, [...(cols.get(level.get(n)!) ?? []), n]);
    const pos = new Map<string, { x: number; y: number }>();
    let maxRows = 1;
    for (const [lv, ns] of cols) {
      maxRows = Math.max(maxRows, ns.length);
      ns.forEach((n, i) => pos.set(n, { x: PAD + lv * (W + GX), y: PAD + i * (H + GY) }));
    }
    const nodes = f.nodes.map(n => ({ ...n, ...pos.get(n.name)!, typeLabel: TYPE[n.type]?.label ?? n.type, color: TYPE[n.type]?.color ?? '#64748b' }));
    const edges = drawn.filter(e => pos.has(e.from) && pos.has(e.to)).map(e => {
      const a = pos.get(e.from)!, b = pos.get(e.to)!;
      const x1 = a.x + W, y1 = a.y + H / 2, x2 = b.x, y2 = b.y + H / 2;
      const back = x2 <= x1;  // same column or backwards: route around below
      const d = back ? `M${x1},${y1} C${x1 + 40},${y1 + 60} ${x2 - 40},${y2 + 60} ${x2},${y2}`
        : `M${x1},${y1} C${x1 + GX / 2},${y1} ${x2 - GX / 2},${y2} ${x2},${y2}`;
      const cond = e.trigger === '6', fail = e.trigger === 'failure';
      return { d, color: fail ? '#dc2626' : cond ? '#d97706' : '#94a3b8', dash: fail || cond ? '5 4' : null,
               marker: fail ? 'ahf' : cond ? 'ahc' : 'ah',
               title: `${e.from} → ${e.to}` + (cond && e.expression ? ` si ${e.expression}` : fail ? ' si falla' : '') };
    });
    const w = PAD * 2 + cols.size * W + (cols.size - 1) * GX, h = PAD * 2 + maxRows * H + (maxRows - 1) * GY + 40;
    return { nodes, edges, w, h };
  });
}

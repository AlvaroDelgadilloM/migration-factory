"""Custom Odoo module code 12 -> 19: inventory of what changes (file and line), the automatic port with OCA odoo-module-migrator,
and what is still left after it (the same patterns searched in the ported code). Nothing here runs the modules' code.

Each pattern says where it applies (the version that changed it), what replaces it and why it matters; after the port a finding
is AUTO (the tool removed it), MANUAL (still there) or REVIEW (always needs a person: raw SQL, external tables, semantics)."""
from __future__ import annotations

import ast
import re
import shutil
import subprocess
from pathlib import Path

MANIFESTS = ('__manifest__.py', '__openerp__.py')
SAFE_NAME = re.compile(r'^[A-Za-z0-9_./-]+$')  # the porting tool renames files through a shell: nothing else gets to it
MIGRATOR = 'odoo-module-migrate'

# id: (files, regex, since, target, note, always_review)
PATTERNS = {
    'api_multi': ('py', r'@api\.multi\b', '13.0', 'se elimina (los métodos ya reciben varios registros)', '', False),
    'api_one': ('py', r'@api\.one\b', '13.0', 'se elimina y el método debe recorrer self',
                'Con @api.one el método corría registro por registro; sin él, recibe todos: revisar que recorra self.', True),
    'track_visibility': ('py', r'track_visibility\s*=', '13.0', 'tracking=True', '', False),
    'sudo_user': ('py', r'\.sudo\(\s*[^)\s]', '13.0', 'with_user(usuario)', 'sudo(usuario) ya no cambia de usuario.', False),
    'self_cr': ('py', r'self\._cr\b', '17.0', 'self.env.cr', '', False),
    'name_get': ('py', r'def\s+name_get\b', '17.0', '_compute_display_name', '', False),
    'fields_view_get': ('py', r'def\s+fields_view_get\b', '16.0', 'get_views / _get_view', '', False),
    'account_invoice': ('py,xml', r'account\.invoice\b|[\'"]account_invoice[\'"]', '13.0', 'account.move',
                        'Las facturas pasan a account.move: number → name, date_invoice → invoice_date, reference → ref, '
                        'action_invoice_open → action_post, estados open/paid → posted + payment_state. Los campos propios '
                        'agregados a la factura necesitan un script que copie sus datos a account_move (account_invoice.move_id).',
                        False),
    'stock_production_lot': ('py,xml', r'stock\.production\.lot\b|[\'"]stock_production_lot[\'"]', '16.0', 'stock.lot',
                             'OpenUpgrade renombra la tabla (los campos propios viajan con ella); el código y las vistas deben '
                             'usar stock.lot.', False),
    'partner_customer': ('py,xml', r'name="(customer|supplier)"|\b(customer|supplier)\s*=\s*(True|False)\b', '13.0',
                         'customer_rank / supplier_rank', 'Los campos customer/supplier de res.partner ya no existen.', False),
    'attrs': ('xml', r'\battrs\s*=', '17.0', 'readonly= / invisible= / required= con expresión', '', False),
    'states_attr': ('xml', r'\bstates\s*=\s*"', '17.0', 'invisible= con expresión sobre state', '', False),
    'tree_view': ('xml', r'<tree\b', '18.0', '<list>', '', False),
    'view_mode_tree': ('xml', r'view_mode[^>]*>[^<]*\btree\b', '18.0', 'list', '', False),
    'assets_template': ('xml', r'inherit_id="web\.assets_', '15.0', "'assets' en el manifiesto", '', False),
    'raw_sql': ('py', r'cr\.execute\(', '—', 'sin cambio automático',
                'SQL directo: no pasa por el ORM. Revisar que las tablas y columnas existan en 19 y los permisos.', True),
    'sql_view': ('py', r'_auto\s*=\s*False', '—', 'sin cambio automático',
                 'Modelo sin tabla propia (vista SQL o tabla externa): init() debe recrear la vista con columnas que existan en 19.',
                 True),
    'external_table': ('py', r"_table\s*=\s*['\"]", '—', 'conservar la tabla',
                       'Tabla fuera del ORM: OpenUpgrade no la migra ni la crea; viaja con el respaldo de la base y en una base '
                       'nueva no existe.', True),
}


def safe_tree(root: Path) -> list[str]:
    """Relative paths whose names the porting tool may see; anything else is refused before it runs."""
    bad = [str(p.relative_to(root)) for p in root.rglob('*') if not SAFE_NAME.match(str(p.relative_to(root)))]
    return bad


def modules(root: Path) -> dict[str, dict]:
    """name -> {path, manifest} for every addon under root (a folder with a manifest)."""
    out = {}
    for m in sorted(p for name in MANIFESTS for p in root.rglob(name)):
        try:
            manifest = ast.literal_eval(m.read_text(encoding='utf-8', errors='replace'))
        except (ValueError, SyntaxError):
            manifest = {}
        out.setdefault(m.parent.name, {'path': m.parent, 'manifest': manifest if isinstance(manifest, dict) else {}})
    return out


def scan(module_path: Path) -> list[dict]:
    """Every pattern occurrence: pattern id, file (relative to the module), line, the line's text."""
    found = []
    for f in sorted(module_path.rglob('*')):
        ext = f.suffix.lstrip('.')
        if not f.is_file() or ext not in ('py', 'xml'):
            continue
        lines = f.read_text(encoding='utf-8', errors='replace').splitlines()
        for key, (kinds, rx, *_rest) in PATTERNS.items():
            if ext not in kinds.split(','):
                continue
            for n, line in enumerate(lines, 1):
                if re.search(rx, line):
                    found.append({'pattern': key, 'file': str(f.relative_to(module_path)), 'line': n, 'text': line.strip()[:200]})
    return found


def inventory(src: Path, ported: Path | None) -> list[dict]:
    """Per module: manifest data and every finding with its status after the port (residual = still found in the ported code)."""
    rows = []
    after = modules(ported) if ported else {}
    for name, m in modules(src).items():
        left = {(f['pattern'], f['file']) for f in scan(after[name]['path'])} if name in after else None
        findings = []
        for f in scan(m['path']):
            kinds, rx, since, target, note, review = PATTERNS[f['pattern']]
            status = 'REVIEW' if review else ('MANUAL' if left is None or (f['pattern'], f['file']) in left else 'AUTO')
            findings.append(f | {'since': since, 'target': target, 'note': note, 'status': status})
        man = m['manifest']
        rows.append({'name': name, 'version': man.get('version', ''), 'depends': list(man.get('depends', [])),
                     'findings': findings, 'counts': {s: sum(1 for f in findings if f['status'] == s) for s in ('AUTO', 'MANUAL', 'REVIEW')}})
    return rows


def port(src: Path, out: Path, run, ctx, log, init='12.0', target='19.0') -> list[str]:
    """odoo-module-migrate on a copy (no git); returns its WARNING/ERROR lines without the absolute paths."""
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(src, out)
    text = ''
    for i, root in enumerate(sorted({m['path'].parent for m in modules(out).values()})):
        res = run([MIGRATOR, '-d', str(root), '-i', init, '-t', target, '-nc', '-ll', 'WARNING'], root, ctx, name=f'odoo-port-{i}', timeout=1800)
        if res.exit_code != 0:
            log(f'la herramienta de port terminó con código {res.exit_code} en {root.name}')
        f = ctx.workdir / 'logs' / f'odoo-port-{i}.log'
        text += f.read_text(encoding='utf-8', errors='replace') if f.exists() else ''
    clean = re.compile(r'\x1b\[[0-9;]*m')
    return sorted({clean.sub('', line).replace(str(out) + '/', '').strip()[:400] for line in text.splitlines()
                   if 'WARNING' in line or 'ERROR' in line})


def diff(src: Path, ported: Path) -> str:
    """Unified diff of the port, original → ported, for review."""
    import difflib
    out = []
    files = sorted({p.relative_to(src) for p in src.rglob('*') if p.is_file()} | {p.relative_to(ported) for p in ported.rglob('*') if p.is_file()})
    for rel in files:
        a, b = src / rel, ported / rel
        if rel.suffix not in ('.py', '.xml', '.csv'):
            continue
        la = a.read_text(encoding='utf-8', errors='replace').splitlines(keepends=True) if a.exists() else []
        lb = b.read_text(encoding='utf-8', errors='replace').splitlines(keepends=True) if b.exists() else []
        out.extend(difflib.unified_diff(la, lb, f'a/{rel}', f'b/{rel}'))
    return ''.join(out)


def install_order(rows: list[dict]) -> list[str]:
    """The custom modules with their custom dependencies first (each one is installed with those it needs)."""
    names, done = {r['name']: r for r in rows}, []

    def visit(n, path=()):
        if n in done or n in path:
            return
        for d in names[n]['depends']:
            if d in names:
                visit(d, path + (n,))
        done.append(n)
    for n in names:
        visit(n)
    return done


def custom_deps(rows: list[dict], name: str) -> list[str]:
    names = {r['name']: r for r in rows}
    out, todo = [], [name]
    while todo:
        n = todo.pop()
        if n not in out:
            out.append(n)
            todo.extend(d for d in names[n]['depends'] if d in names)
    return out


if __name__ == '__main__':  # self-check on a tiny module
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        src, ported = Path(t) / 'src' / 'm_a', Path(t) / 'port' / 'm_a'
        for d, body in ((src, "@api.multi\ndef x(self):\n    self._cr.execute('select 1')\n"), (ported, "def x(self):\n    self.env.cr.execute('select 1')\n")):
            d.mkdir(parents=True)
            (d / '__manifest__.py').write_text("{'version': '12.0.1.0.0', 'depends': ['sale']}")
            (d / 'm.py').write_text(body)
        rows = inventory(src.parent, ported.parent)
        st = {f['pattern']: f['status'] for f in rows[0]['findings']}
        assert st == {'api_multi': 'AUTO', 'self_cr': 'AUTO', 'raw_sql': 'REVIEW'}, st
        assert install_order([{'name': 'b', 'depends': ['a']}, {'name': 'a', 'depends': ['sale']}]) == ['a', 'b']
        assert custom_deps([{'name': 'b', 'depends': ['a']}, {'name': 'a', 'depends': []}], 'b') == ['b', 'a']
        assert safe_tree(src.parent) == []
    print('ok')

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


def _states_to_invisible(m: re.Match) -> str:
    """<tag ... states="a,b" ...> → invisible="state not in ('a', 'b')" (17.0 removed states), unless the tag already has invisible."""
    tag = m.group(0)
    st = re.search(r'\sstates="([^"]*)"', tag)
    if not st or re.search(r'\sinvisible=', tag):
        return tag
    values = ', '.join(f"'{v.strip()}'" for v in st[1].split(',') if v.strip())
    return tag.replace(st.group(0), f' invisible="state not in ({values}{"," if values.count(",") == 0 else ""})"')


# What OCA odoo-module-migrator leaves (it warns but does not rewrite): the platform's own rules, per version where the change
# happened. (since, scope: the file must mention it, files, regex, replacement). Applied right after the tool's step to that version.
INVOICE = r'account\.invoice\b'
RULES = [
    ('13.0', INVOICE, 'py', r"""(_inherit\s*=\s*)(['"])account\.invoice\2""", r"\1\2account.move\2"),
    ('13.0', INVOICE, 'py', r"""(env\[\s*)(['"])account\.invoice\2""", r"\1\2account.move\2"),
    ('13.0', INVOICE, 'py', r'\baction_invoice_open\b', 'action_post'),
    ('13.0', INVOICE, 'py', r'\.date_invoice\b', '.invoice_date'),
    ('13.0', INVOICE, 'py', r'\.number\b', '.name'),
    ('13.0', INVOICE, 'xml', r'(<field name="model">)account\.invoice(</field>)', r'\1account.move\2'),
    ('13.0', INVOICE, 'xml', r'ref="account\.invoice_form"', 'ref="account.view_move_form"'),
    ('13.0', INVOICE, 'xml', r'(<field name="name">)account\.invoice\.', r'\1account.move.'),
    ('13.0', INVOICE, 'xml', r'ref="account\.invoice_tree"', 'ref="account.view_invoice_tree"'),
    ('13.0', INVOICE, 'xml', r"""@name=(['"])reference\1""", r"@name=\1ref\1"),
    ('13.0', INVOICE, 'xml', r"""@name=(['"])date_invoice\1""", r"@name=\1invoice_date\1"),
    ('13.0', INVOICE, 'xml', r"""\(\s*'state'\s*,\s*'in'\s*,\s*\[\s*'open'\s*,\s*'paid'\s*\]\s*\)""", "('state', '=', 'posted')"),
    ('13.0', r'name="(customer|supplier)"', 'xml', r'<field name="customer">\s*(True|1)\s*</field>', '<field name="customer_rank">1</field>'),
    ('13.0', r'name="(customer|supplier)"', 'xml', r'<field name="supplier">\s*(True|1)\s*</field>', '<field name="supplier_rank">1</field>'),
    ('13.0', r'name="(customer|supplier)"', 'xml', r'<field name="(customer|supplier)">\s*(False|0)\s*</field>\n?', ''),
    ('16.0', r'stock\.production\.lot', 'xml', r'(<field name="model">)stock\.production\.lot(</field>)', r'\1stock.lot\2'),
    ('16.0', r'stock\.production\.lot', 'xml', r'(<field name="name">)stock\.production\.lot\.', r'\1stock.lot.'),
    ('17.0', r'\sstates="', 'xml', r'<[^<>]*\sstates="[^"]*"[^<>]*>', _states_to_invisible),
    ('16.0', r'stock\.production\.lot', 'py', r"""(['"])stock\.production\.lot\1""", r"\1stock.lot\1"),
]
FIELD = re.compile(r'^(\s+)(\w+)\s*=\s*fields\.(\w+)\(')


def _tool(d: Path, init: str, target: str, run, ctx, log) -> list[str]:
    """One step of odoo-module-migrate on d (no git); its WARNING/ERROR lines without the absolute paths."""
    text = ''
    for i, root in enumerate(sorted({m['path'].parent for m in modules(d).values()})):
        name = f'odoo-port-{target}-{i}'
        res = run([MIGRATOR, '-d', str(root), '-i', init, '-t', target, '-nc', '-ll', 'WARNING'], root, ctx, name=name, timeout=1800)
        if res.exit_code != 0:
            log(f'la herramienta de port a {target} terminó con código {res.exit_code} en {root.name}')
        f = ctx.workdir / 'logs' / f'{name}.log'
        text += f.read_text(encoding='utf-8', errors='replace') if f.exists() else ''
    clean = re.compile(r'\x1b\[[0-9;]*m')
    return [f'[{target}] ' + re.sub(r'^\d\d:\d\d:\d\d\s+', '', clean.sub('', line).replace(str(d) + '/', '').strip())[:400]
            for line in text.splitlines() if 'WARNING' in line or 'ERROR' in line]


def apply_rules(d: Path, version: str) -> list[str]:
    """The platform's rules of that version on every module file; returns what it changed (file and rule)."""
    done = []
    for f in sorted(d.rglob('*')):
        ext = f.suffix.lstrip('.')
        if not f.is_file() or ext not in ('py', 'xml'):
            continue
        text = original = f.read_text(encoding='utf-8', errors='replace')
        for since, scope, kind, rx, repl in RULES:
            if since == version and kind == ext and re.search(scope, original):
                text, n = re.subn(rx, repl, text)
                if n:
                    done.append(f"[{version}] regla de la plataforma: {f.relative_to(d)} · {rx[:60]} → "
                                f"{repl[:40] if isinstance(repl, str) else repl.__doc__.split(' (')[0][:60]} ({n})")
        if text != original:
            f.write_text(text, encoding='utf-8')
    return done


def _stored_fields(py: Path) -> list[str]:
    """Fields with a column declared in the file (not One2many/Many2many, not computed without store=True)."""
    lines, out = py.read_text(encoding='utf-8', errors='replace').splitlines(), []
    for i, line in enumerate(lines):
        m = FIELD.match(line)
        if not m or m[3] in ('One2many', 'Many2many'):
            continue
        stmt = ' '.join(lines[i:i + 8])
        stmt = stmt[:stmt.find(')', stmt.find('fields.')) + 1] if ')' in stmt else stmt
        if 'compute=' in stmt and 'store=True' not in stmt or 'related=' in stmt and 'store=True' not in stmt:
            continue
        out.append(m[2])
    return out


def data_scripts(prev: Path, d: Path, version: str) -> list[str]:
    """Migration scripts for the custom data of models Odoo merged into another one at this version: at 13.0 account.invoice →
    account.move (OpenUpgrade keeps the mapping in account_move.old_invoice_id). Written into the module of this version, so the
    hop runs them after updating the module (its columns exist by then)."""
    if version != '13.0':
        return []
    notes = []
    before, now = modules(prev), modules(d)
    for name, m in before.items():
        cols = []
        for py in m['path'].rglob('*.py'):
            if re.search(r"""_inherit\s*=\s*(['"])account\.invoice\1""", py.read_text(encoding='utf-8', errors='replace')):
                cols += _stored_fields(py)
        if not cols or name not in now:
            continue
        ver = str(now[name]['manifest'].get('version') or '13.0.1.0.0')
        target = now[name]['path'] / 'migrations' / ver / 'post-migration.py'
        target.parent.mkdir(parents=True, exist_ok=True)
        sets = ', '.join(f'"{c}" = ai."{c}"' for c in cols)
        target.write_text(
            '# Generated by Migration Factory: custom fields of account.invoice copied to account.move. OpenUpgrade 13.0 merged the\n'
            '# models and keeps the mapping in account_move.old_invoice_id; account_invoice still holds the old values.\n\n\n'
            'def migrate(cr, version):\n'
            "    cr.execute(\"select 1 from information_schema.columns where table_name = 'account_move' and column_name = 'old_invoice_id'\")\n"
            '    if not cr.fetchone():\n'
            '        return\n'
            f'    cr.execute(\'UPDATE account_move am SET {sets} FROM account_invoice ai WHERE am.old_invoice_id = ai.id\')\n',
            encoding='utf-8')
        notes.append(f'[13.0] script de datos generado: {name}/migrations/{ver}/post-migration.py copia {", ".join(cols)} de '
                     'account_invoice a account_move')
    return notes


def port_steps(src: Path, out: Path, run, ctx, log, start='12.0', target='19.0') -> dict:
    """The code version by version (each hop of a database migration needs its modules' code for that release): the OCA tool's
    step, then the platform's rules and the generated data scripts of that version. Returns {'snapshots': {version: dir},
    'notes': [...]}; snapshots[target] is the final code."""
    from .modules import VERSIONS
    shutil.rmtree(out, ignore_errors=True)
    prev, prev_v, snaps, notes = src, start, {}, []
    for v in [v for v in VERSIONS if float(start) < float(v) <= float(target)]:
        d = out / v
        shutil.copytree(prev, d)
        notes += _tool(d, prev_v, v, run, ctx, log)
        notes += apply_rules(d, v)
        notes += data_scripts(prev, d, v)
        snaps[v], prev, prev_v = d, d, v
    log(f"código portado versión por versión: {', '.join(snaps)}")
    return {'snapshots': snaps, 'notes': notes}


SMOKE = '''# Generated by Migration Factory for the install check on Odoo 19 (not part of the ported code you download).
from odoo.tests import TransactionCase, tagged

MODULE = %r


@tagged('post_install', '-at_install')
class TestMigrationFactorySmoke(TransactionCase):
    """Every model the module defines or extends: its records read with every field (computed ones run) and its form and list
    views built, on a database with demo data."""

    def test_read_every_field_and_build_views(self):
        data = self.env['ir.model.data'].sudo().search([('module', '=', MODULE), ('model', 'in', ('ir.model', 'ir.model.fields'))])
        models = sorted({rec.model for d in data for rec in self.env[d.model].sudo().browse(d.res_id).exists()})
        problems, checked = [], []
        for name in models:
            if name not in self.env or self.env[name]._abstract:
                continue
            Model = self.env[name]
            try:
                with self.env.cr.savepoint():
                    recs = Model.search([], limit=20)
                    recs.read([f for f, fld in Model._fields.items() if fld.type != 'binary'])
                    checked.append(f'{name} ({len(recs)})')
            except Exception as e:  # noqa: BLE001 - every problem is listed, not only the first
                problems.append(f'{name}: leer registros y campos: {e}')
            for view in ('form', 'list'):
                try:
                    Model.get_views([(False, view)])
                except Exception as e:  # noqa: BLE001
                    problems.append(f'{name}: vista {view}: {e}')
        print('MF_SMOKE %%s: %%s' %% (MODULE, ', '.join(checked)))
        self.assertFalse(problems, '\\n'.join(problems))
'''


def add_smoke_test(module_dir: Path, name: str) -> None:
    """The generated functional test inside a COPY of a ported module (the install check's), next to the module's own tests."""
    tests = module_dir / 'tests'
    tests.mkdir(exist_ok=True)
    (tests / 'test_mf_smoke.py').write_text(SMOKE % name, encoding='utf-8')
    init = tests / '__init__.py'
    text = init.read_text(encoding='utf-8') if init.exists() else ''
    if 'test_mf_smoke' not in text:
        init.write_text(text + ('\n' if text and not text.endswith('\n') else '') + 'from . import test_mf_smoke\n', encoding='utf-8')


def test_results(log: str) -> dict | None:
    """Odoo's test summary in an install log ("N failed, M error(s) of T tests"), the failing tests and what the smoke test read."""
    m = list(re.finditer(r'(\d+) failed, (\d+) error\(s\) of (\d+) tests', log))
    if not m:
        return None
    failed, errors, total = (int(x) for x in m[-1].groups())
    fails = [re.sub(r'^.*?(FAIL|ERROR):', r'\1:', line).strip()[:300] for line in log.splitlines()
             if re.search(r'\b(FAIL|ERROR): ', line)][:20]
    smoke = [line.split('MF_SMOKE', 1)[1].strip()[:500] for line in log.splitlines() if 'MF_SMOKE' in line]
    return {'total': total, 'failed': failed, 'errors': errors, 'failures': fails, 'smoke': smoke}


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
    x = re.sub(r'<[^<>]*\sstates="[^"]*"[^<>]*>', _states_to_invisible, '<button name="a" states="draft"/><button states="a,b" invisible="1"/>')
    assert x == '<button name="a" invisible="state not in (\'draft\',)"/><button states="a,b" invisible="1"/>', x
    print('ok')

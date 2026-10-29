"""External SQL of an Odoo project (objects outside the ORM: schemas, tables, sequences, functions, triggers, views, materialized
views, seed data), read from SQL files that come with the module code. Nothing here runs the SQL: it is parsed, cross-referenced
with the modules' code and classified with the reason, in the categories the migration plan uses:

KEEP · MIGRATE (recreate around the hops) · REWRITE (touches what changes in 19) · MOVE_TO_ORM (candidate) · DEPRECATE (unused by
the modules) · MANUAL_REVIEW. The classification is a rule with its reason, never a score."""
from __future__ import annotations

import re
from pathlib import Path

# Odoo tables that change between 12 and 19 (name → what happens), for SQL that reads or stores them
CHANGED = {
    'account_invoice': 'desde 13.0 las facturas son account_move (otros IDs; account_move.old_invoice_id guarda el original)',
    'account_invoice_line': 'desde 13.0 las líneas de factura son account_move_line',
    'stock_production_lot': 'desde 16.0 la tabla es stock_lot (mismos IDs)',
    'account_invoice_tax': 'desde 13.0 los impuestos de factura son líneas de account_move_line',
}
# columns that store IDs of records whose IDs change in the migration
ID_REMAP = [(re.compile(r'^(?:account_)?invoice(?:_odoo)?_id$'), 'account_invoice')]   # not external_invoice_id: another system's code

OBJ = re.compile(
    r'^\s*create\s+(?:or\s+replace\s+)?(?:(?P<mat>materialized)\s+)?(?P<kind>schema|table|sequence|function|trigger|view)\s+'
    r'(?:if\s+not\s+exists\s+)?(?P<name>[\w."]+)', re.I | re.S)
REF = re.compile(r'\b(?:from|join|into|update|table)\s+(?:only\s+)?([\w."]+)', re.I)
NOT_NAMES = {'each', 'row', 'update', 'delete', 'insert', 'set', 'select', 'on', 'only', 'lateral'}


SKIP = re.compile(r'^\s*(create|drop)\s+database\b', re.I)   # another database: not run inside the one being checked


def statements(sql: str) -> list[tuple[int, str]]:
    """(first line, text) of each statement, splitting on ; outside dollar-quoted bodies and comments. psql meta-commands
    (\\connect, \\set…) are not SQL: their lines are left out (line numbers kept)."""
    sql = re.sub(r'(?m)^[ \t]*\\.*$', '', sql)
    out, buf, line, start, dollar = [], [], 1, 1, None
    i = 0
    while i < len(sql):
        if dollar is None and sql.startswith('--', i):
            j = sql.find('\n', i)
            i = len(sql) if j == -1 else j
            continue
        m = re.match(r'\$\w*\$', sql[i:])
        if m:
            tag = m.group(0)
            dollar = None if dollar == tag else (tag if dollar is None else dollar)
            buf.append(tag)
            i += len(tag)
            continue
        ch = sql[i]
        if ch == ';' and dollar is None:
            text = ''.join(buf).strip()
            if text:
                out.append((start, text))
            buf, start = [], line + (1 if sql[i + 1:i + 2] == '\n' else 0)
        else:
            if not buf and ch.isspace():
                start = line + (1 if ch == '\n' else 0)
            else:
                buf.append(ch)
        line += ch == '\n'
        i += 1
    if ''.join(buf).strip():
        out.append((start, ''.join(buf).strip()))
    return out


def _split(name: str) -> tuple[str, str]:
    name = name.replace('"', '').lower()
    return tuple(name.split('.', 1)) if '.' in name else ('public', name)  # type: ignore[return-value]


def parse(files: dict[str, str]) -> list[dict]:
    """files: {path: sql text}. One row per object: kind, schema, name, file, line, columns, references, seeded rows."""
    objects, rows_by_table = {}, {}
    for path, sql in files.items():
        for line, st in statements(sql):
            low = st.lower()
            m = OBJ.match(st)
            if m:
                kind = 'materialized view' if m['mat'] else m['kind'].lower()
                schema, name = (m['name'].replace('"', '').lower(), '') if kind == 'schema' else _split(m['name'])
                on = re.search(r'\bon\s+([\w."]+)', st, re.I) if kind == 'trigger' else None
                if on:   # create trigger name ... on schema.table: the trigger lives with its table
                    schema = _split(on[1])[0]
                body = st[m.end():]
                refs = {'.'.join(_split(r)) for r in REF.findall(body) if r.lower() not in NOT_NAMES}
                if kind == 'trigger' and on:
                    refs.add('.'.join(_split(on[1])))
                    proc = re.search(r'execute\s+(?:procedure|function)\s+([\w."]+)', st, re.I)
                    refs |= {'.'.join(_split(proc[1]))} if proc else set()
                refs = sorted(refs)
                cols = []
                if kind == 'table':
                    inner = body[body.find('(') + 1: body.rfind(')')]
                    cols = [c.split()[0].strip('"').lower() for c in re.split(r',(?![^()]*\))', inner)
                            if c.strip() and c.split()[0].lower().split('(')[0] not in ('primary', 'unique', 'constraint', 'foreign', 'check')]
                key = (kind, schema, name)
                objects[key] = {'kind': kind, 'schema': schema, 'name': name, 'file': path, 'line': line, 'columns': cols,
                                'references': [r for r in refs if r != f'{schema}.{name}'], 'rows': 0,
                                'body': st[:4000]}
            elif low.startswith('insert into'):
                t = re.match(r'insert\s+into\s+([\w."]+)', st, re.I)
                values = st[st.lower().find('values') + 6:] if 'values' in low else ''
                n = len(re.findall(r'\)\s*(?:,|$)', values.strip())) if values else 0
                if t:
                    rows_by_table['.'.join(_split(t[1]))] = rows_by_table.get('.'.join(_split(t[1])), 0) + n
    for (kind, schema, name), o in objects.items():
        if kind == 'table':
            o['rows'] = rows_by_table.get(f'{schema}.{name}', 0)
    return sorted(objects.values(), key=lambda o: (o['file'], o['line']))


def _uses(code: dict[str, str], obj: dict) -> list[dict]:
    """Where the modules' code names the object (schema-qualified or bare): module, file, line, how."""
    full, bare = f"{obj['schema']}.{obj['name']}", obj['name']
    out = []
    for path, text in code.items():
        for n, line in enumerate(text.splitlines(), 1):
            low = line.lower()
            if full in low or re.search(rf"""_table\s*=\s*['"]{re.escape(bare)}['"]""", low) or (
                    bare and re.search(rf'\b{re.escape(bare)}\b', low) and obj['kind'] != 'schema' and ('execute' in low or 'from ' in low
                                                                                                    or 'into ' in low or '_table' in low)):
                how = ('model' if '_table' in low else 'write' if re.search(r'\b(insert|update|delete)\b', low)
                       else 'refresh' if 'refresh' in low else 'call' if bare + '(' in low else 'read')
                out.append({'module': path.split('/')[0], 'file': path, 'line': n, 'how': how})
    return out


def classify(objects: list[dict], code: dict[str, str]) -> list[dict]:
    """code: {module/relative path: text} of the modules (.py/.xml). Adds category, reason, uses."""
    external = {f"{o['schema']}.{o['name']}" for o in objects if o['kind'] in ('table', 'view', 'materialized view')}

    def needs(x, o):   # x depends on o: names it in its references or its body (a sequence default, a trigger's function)
        return x is not o and o['kind'] != 'schema' and (f"{o['schema']}.{o['name']}" in x['references']
                                                         or re.search(rf"\b{re.escape(o['schema'])}\.{re.escape(o['name'])}\b", (x.get('body') or '').lower()))
    for o in objects:
        o['uses'] = _uses(code, o)
    # what the modules use, and everything those objects need (fixpoint over the dependencies)
    used = {id(o) for o in objects if o['uses'] or o['kind'] == 'schema'}
    changed_set = True
    while changed_set:
        changed_set = False
        for o in objects:
            # needed by something in use; or a trigger on a table in use (it fires with every write)
            if id(o) not in used and any(id(x) in used and (needs(x, o) or (o['kind'] == 'trigger' and needs(o, x))) for x in objects):
                used.add(id(o))
                changed_set = True
    by_table = {}
    for o in objects:
        uses = o['uses']
        # writes with direct SQL may span lines: a module that executes SQL naming the table writes it if INSERT/UPDATE is near
        for u in uses:
            if u['how'] == 'read':
                lines = code[u['file']].splitlines()
                near = ' '.join(lines[max(0, u['line'] - 3): u['line'] + 1]).lower()
                if re.search(r'\b(insert\s+into|update|delete\s+from)\b', near):
                    u['how'] = 'write'
        odoo_refs = sorted(r.split('.', 1)[1] for r in o['references'] if r.startswith('public.') and r not in external)
        changed = [t for t in odoo_refs if t in CHANGED]
        o['uses'], o['odooTables'] = uses, odoo_refs
        modules = sorted({u['module'] for u in uses})
        remap = [c for c in o['columns'] for rx, _ in ID_REMAP if rx.search(c)]
        k = o['kind']
        if changed:
            cat, why = 'REWRITE', '; '.join(f'usa {t}: {CHANGED[t]}' for t in changed)
        elif remap:
            cat, why = 'REWRITE', (f"guarda IDs de facturas ({', '.join(remap)}): {CHANGED['account_invoice']}; remapear esos valores con "
                                   'account_move.old_invoice_id después del salto 13')
        elif k in ('view', 'materialized view') and odoo_refs:
            cat, why = 'MIGRATE', (f"depende de tablas de Odoo ({', '.join(odoo_refs)}): quitarla antes de los saltos (OpenUpgrade cambia columnas) "
                                   'y recrearla al final')
        elif k == 'table' and any(u['how'] == 'model' for u in uses):
            m = next(u for u in uses if u['how'] == 'model')
            cat, why = 'KEEP', f"la usa el modelo de {m['module']} ({m['file']}:{m['line']}, _table sin tabla del ORM): se conserva con sus datos"
            decl = code[m['file']].splitlines()[m['line'] - 1]
            if o['schema'] != 'public' and f"{o['schema']}." not in decl.lower():
                why += (f"; ojo: _table no indica el esquema {o['schema']}: el ORM la busca en el search_path (public), revisar que "
                        'apunte a la tabla correcta en 19')
        elif k == 'table' and any(u['how'] == 'write' for u in uses):
            cat, why = 'MOVE_TO_ORM', (f"la escriben con SQL directo {', '.join(modules)}: candidata a modelo del ORM (permisos, auditoría, "
                                       'multiempresa); KEEP si la usa otro sistema')
        elif k in ('function', 'trigger') and odoo_refs:
            cat, why = 'MANUAL_REVIEW', f"código PL/pgSQL sobre tablas de Odoo ({', '.join(odoo_refs)}): revisar contra el esquema de 19"
        elif id(o) in used or (k == 'trigger' and any(id(x) in used and needs(o, x) for x in objects)):
            deps = [f"{x['kind']} {x['schema']}.{x['name']}" for x in objects if needs(x, o) and id(x) in used]
            by = ', '.join(modules) if modules else ''
            cat, why = 'KEEP', ('la usa ' + by if by else 'la necesita ' + ', '.join(deps) if deps else 'acompaña a su tabla') + \
                ('; solo toca objetos externos' if k in ('function', 'trigger', 'view', 'materialized view') else '')
        else:
            cat, why = 'DEPRECATE', 'ningún módulo la usa: confirmar que no la use otro sistema antes de retirarla'
        if k == 'trigger' and 'execute procedure' in (o.get('body') or '').lower():
            why += '; EXECUTE PROCEDURE es sintaxis antigua (válida en PostgreSQL 13, preferible EXECUTE FUNCTION)'
        if k == 'materialized view' and any(u['how'] == 'refresh' for u in uses):
            why += '; la refresca ' + ', '.join(sorted({u['module'] for u in uses if u['how'] == 'refresh'}))
        o['category'], o['reason'] = cat, why
        by_table[f"{o['schema']}.{o['name']}"] = o
    return objects


def module_code(root: Path) -> dict[str, str]:
    """{module/path: text} of the .py and .xml files of every addon under root."""
    from .code import modules
    out = {}
    for name, m in modules(root).items():
        for f in sorted(m['path'].rglob('*')):
            if f.is_file() and f.suffix in ('.py', '.xml'):
                out[f'{name}/{f.relative_to(m["path"])}'] = f.read_text(encoding='utf-8', errors='replace')
    return out


def is_odoo_dump(head: bytes) -> bool:
    """A full Odoo database (plain pg_dump) names ir_module_module early; pg_dump custom format starts with PGDMP."""
    return head.startswith(b'PGDMP') or b'ir_module_module' in head


if __name__ == '__main__':  # self-check
    sql = """CREATE SCHEMA IF NOT EXISTS legacy_ext;
-- comment; with semicolon
CREATE TABLE IF NOT EXISTS legacy_ext.invoice_audit (id BIGSERIAL PRIMARY KEY, invoice_odoo_id INTEGER, amount NUMERIC(16,2), UNIQUE(id));
CREATE TABLE legacy_ext.msg (id int, status text);
CREATE TABLE legacy_ext.unused (id int);
CREATE OR REPLACE FUNCTION legacy_ext.f() RETURNS TRIGGER AS $$ BEGIN NEW.x = 1; RETURN NEW; END; $$ LANGUAGE plpgsql;
CREATE TRIGGER t BEFORE UPDATE ON legacy_ext.msg FOR EACH ROW EXECUTE PROCEDURE legacy_ext.f();
CREATE VIEW legacy_ext.v AS SELECT p.id FROM res_partner p JOIN legacy_ext.msg m ON m.id = p.id;
INSERT INTO legacy_ext.msg (id, status) VALUES (1,'a'), (2,'b');
"""
    objs = {o['name'] or o['schema']: o for o in classify(parse({'x.sql': sql}), {
        'mod_a/models/m.py': "    _table = 'msg'\n    _auto = False\n",
        'mod_b/models/b.py': "        self._cr.execute('''INSERT INTO legacy_ext.invoice_audit\n (a) VALUES (1)''')\n"})}
    assert objs['invoice_audit']['category'] == 'REWRITE' and objs['invoice_audit']['columns'] == ['id', 'invoice_odoo_id', 'amount']
    assert objs['msg']['category'] == 'KEEP' and objs['msg']['rows'] == 2 and 'public' in objs['msg']['reason']
    assert objs['unused']['category'] == 'DEPRECATE' and objs['v']['category'] == 'MIGRATE' and objs['v']['odooTables'] == ['res_partner']
    assert objs['t']['category'] == 'KEEP' and 'EXECUTE PROCEDURE' in objs['t']['reason'] and objs['f']['category'] == 'KEEP'
    assert is_odoo_dump(b'PGDMP...') and not is_odoo_dump(b'CREATE SCHEMA legacy_ext;')
    print('ok')

"""Parallel job -> SQL pushdown (doc 58). Each writer stage (Oracle target, DataSet, sequential file) becomes one step whose
SELECT is composed backwards over the job's links as nested sub-queries. A stage without a verified translation makes the
step MANUAL: its SQL is kept as a commented draft and the generated task fails with the manual action instead of running
half-translated SQL. Parameters become Jinja (Airflow renders them); credentials never appear in SQL text."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .expressions import Untranslatable, stage_vars_sql, translate
from .registry import SECRET_MEMBER, WRITE_MODES, param_resolver

_TOKEN = re.compile(r'#([A-Za-z_]\w*)(?:\.(\$?\w+))?#')
OSH_PAIR = re.compile(r'\(3\)([^()]+?)\(2\)([^()]*)')


def ident(name: str) -> str:
    return re.sub(r'[^0-9a-zA-Z_]+', '_', name).strip('_').lower() or 'x'


def osh(raw: str | None) -> list[tuple[str, str]]:
    """OSH option strings `(2)(2)0(1)(3)key(2)LOG_ID(2)0...` -> [('key','LOG_ID'), ('asc\\desc','asc'), ...]."""
    return OSH_PAIR.findall(raw or '')


def osh_value(raw: str | None, name: str) -> str | None:
    if raw and '(2)' not in raw:
        return raw
    return next((v for k, v in osh(raw) if k == name), None)


COMPOSABLE = {'OracleConnectorPX', 'PxDataSet', 'PxSequentialFile', 'PxPeek', 'PxCopy', 'PxSort', 'CTransformerStage', 'PxRemDup',
              'PxFilter', 'PxJoin', 'PxMerge', 'PxColumnGenerator', 'PxRowGenerator'}


class Jinja:
    """`#SET.$MEMBER#` -> Airflow Variable, `#PARAM#` -> DAG param, `#ACTIVITY.var#` -> XCom of that activity."""

    def __init__(self, job, activities: dict | None = None):
        self.sets = {p.name for p in job.parameters if p.is_set}
        self.plain = {p.name for p in job.parameters if not p.is_set}
        self.activities = activities or {}
        self.variables, self.issues = set(), []

    def var_name(self, s, m):
        return f"{s}__{m.lstrip('$')}"

    def __call__(self, text: str | None) -> str:
        def rep(mt):
            first, member = mt[1], mt[2]
            if first in self.sets and member:
                if SECRET_MEMBER.search(member.lstrip('$')):
                    self.issues.append(f'referencia a credencial {first}.{member} en texto: va en una Connection')
                    return mt[0]
                self.variables.add(self.var_name(first, member))
                return f'{{{{ var.value.{self.var_name(first, member)} }}}}'
            if first in self.plain and not member:
                return f'{{{{ params.{first} }}}}'
            if first in self.activities and member:
                return f"{{{{ ti.xcom_pull(task_ids='{self.activities[first]}', key='{member}') }}}}"
            self.issues.append(f'parámetro no resuelto: {mt[0]}')
            return mt[0]
        return _TOKEN.sub(rep, text or '')


@dataclass
class Step:
    name: str                 # task id inside the job's TaskGroup
    kind: str                 # sql | file_load | file_export | skip
    stage: str
    sql: str = ''
    status: str = 'OK'        # OK | REVIEW | MANUAL
    issues: list = field(default_factory=list)
    target: str | None = None
    columns: list = field(default_factory=list)
    keys: list = field(default_factory=list)
    resource: str | None = None


class Composer:
    def __init__(self, job, staging_prefix='STG_'):
        self.job, self.jinja, self.prefix = job, Jinja(job), staging_prefix
        self.params = param_resolver(job, self.jinja.variables)  # Variables read by expressions are declared too
        self.staging = {}   # table -> columns (DDL)
        self.loads = []     # file_load steps needed before the SQL steps

    def link(self, name):
        return next(l for l in self.job.links if l.name == name)

    def stg(self, path, columns):
        base = re.sub(r'\.ds$', '', path.rsplit('/', 1)[-1])
        table = self.prefix + re.sub(r'[^0-9A-Za-z_#.$]+', '_', base).upper()
        table = self.jinja(table)
        self.staging.setdefault(table, columns)
        return table

    def cols(self, link, alias='src'):
        return ', '.join(f'{alias}.{c["name"]}' for c in link.columns) or f'{alias}.*'

    def select(self, link_name, st: Step, depth=0) -> str:
        if depth > 40:
            raise Untranslatable('cadena de etapas demasiado profunda o cíclica')
        l = self.link(link_name)
        src = self.job.stage(l.source_stage)
        t = src.stage_type
        ins = [self.link(n) for n in src.input_links]  # pin order
        pad = '  '
        if t == 'OracleConnectorPX':
            sel = src.properties.get('Usage/SQL/SelectStatement')
            if sel:
                return f'SELECT * FROM (\n{self.jinja(sel).strip()}\n)'
            table = src.properties.get('Usage/TableName')
            if table:
                return f"SELECT {', '.join(c['name'] for c in l.columns) or '*'} FROM {self.jinja(table)}"
            raise Untranslatable(f'{src.name}: lectura Oracle sin SELECT ni tabla')
        if t == 'PxDataSet':
            return f"SELECT {', '.join(c['name'] for c in l.columns) or '*'} FROM {self.stg(l.properties.get('dataset', src.name), l.columns)}"
        if t == 'PxSequentialFile':
            table = f'{self.prefix}FILE_{ident(src.name).upper()}'
            self.staging.setdefault(table, l.columns)
            path = osh_value(l.properties.get('file'), 'file') or osh_value(l.properties.get('filepattern'), 'filepattern')
            if any(x.stage == src.name for x in self.loads):  # the same file feeds several outputs: load it once
                st.status = max_status(st.status, 'REVIEW')
                return f"SELECT {', '.join(c['name'] for c in l.columns) or '*'}, MF_SEQ FROM {table}"
            self.loads.append(Step(name=f'load_{ident(src.name)}', kind='file_load', stage=src.name, status='REVIEW', target=table,
                                   columns=[c['name'] for c in l.columns], resource=self.jinja(path) if path else None,
                                   issues=['formato del archivo (delimitador, comillas, encabezado) en config/files.yaml']))
            st.status = max_status(st.status, 'REVIEW')
            return f"SELECT {', '.join(c['name'] for c in l.columns) or '*'}, MF_SEQ FROM {table}"
        if len(ins) == 1 and t in ('PxCopy', 'PxSort', 'PxPeek'):
            inner = self.select(ins[0].name, st, depth + 1)
            return f'SELECT {self.cols(l)}\nFROM (\n{indent(inner, pad)}\n) src'
        if t == 'CTransformerStage' and len(ins) == 1:
            # two levels: stage variables (they may use LAG) are columns of the inner SELECT, so derivations and the
            # constraint (a WHERE) only read plain columns
            order = row_order(self.job, ins[0])
            try:
                svs, lag = stage_vars_sql(src.stage_vars, self.params, order)
            except Untranslatable as e:
                raise Untranslatable(f'{src.name}: {e}') from None
            if lag:
                st.status = max_status(st.status, 'REVIEW')
                st.issues.append(f'{src.name}: valor de la fila anterior traducido a LAG() OVER (ORDER BY {order}); en la primera fila '
                                 'la comparación con NULL se evalúa como en SQL: confirmar con la reconciliación')
            cols = {'*': 'src'} | {n: f'src.MF_SV_{n}' for n in svs}
            items = [f"{translate(c['derivation'], cols, self.params)} AS {c['name']}" if c.get('derivation') else f"src.{c['name']} AS {c['name']}"
                     for c in l.columns]
            where = f'\nWHERE {translate(l.constraint, cols, self.params)}' if l.constraint else ''
            inner = self.select(ins[0].name, st, depth + 1)
            sv_cols = ''.join(f',\n         {sql} AS MF_SV_{n}' for n, sql in svs.items())
            return (f"SELECT {', '.join(items)}\nFROM (\n  SELECT src.*{sv_cols}\n  FROM (\n{indent(inner, pad * 2)}\n  ) src\n) src{where}")
        if t == 'PxMerge' and len(ins) >= 2:
            keys = [v for k, v in osh(src.properties.get('key')) if k == 'key']
            if not keys:
                raise Untranslatable(f'{src.name}: Merge sin claves')
            master, updates = ins[0], ins[1:]
            outs = [self.link(n) for n in src.output_links]
            on = lambda a, b: ' AND '.join(f'{a}.{k} = {b}.{k}' for k in keys)
            st.status = max_status(st.status, 'REVIEW')
            if outs.index(l) == 0:
                keep = src.properties.get('dropBadMasters\\keepBadMasters', 'keepBadMasters') == 'keepBadMasters'
                mcols = {c['name'] for c in master.columns}
                sel = ', '.join(c['name'] if c['name'] in keys else f"{'m' if c['name'] in mcols else 'u'}.{c['name']}" for c in l.columns)
                st.issues.append(f"{src.name}: Merge maestro = {master.name}, actualización = {updates[0].name}; "
                                 f"{'se conservan' if keep else 'se descartan'} los maestros sin actualización")
                a, b = self.select(master.name, st, depth + 1), self.select(updates[0].name, st, depth + 1)
                return (f"SELECT {sel}\nFROM (\n{indent(a, pad)}\n) m\n{'LEFT JOIN' if keep else 'JOIN'} (\n{indent(b, pad)}\n) u "
                        f"USING ({', '.join(keys)})")
            upd = updates[min(outs.index(l) - 1, len(updates) - 1)]
            st.issues.append(f'{src.name}: salida de rechazo = filas de {upd.name} sin maestro')
            a, b = self.select(master.name, st, depth + 1), self.select(upd.name, st, depth + 1)
            return (f"SELECT {', '.join('u.' + c['name'] for c in l.columns)}\nFROM (\n{indent(b, pad)}\n) u\n"
                    f"WHERE NOT EXISTS (\n  SELECT 1 FROM (\n{indent(a, pad * 2)}\n  ) m WHERE {on('m', 'u')}\n)")
        if t == 'PxRemDup' and len(ins) == 1:
            keys = [v for k, v in osh(src.properties.get('key')) if k == 'key']
            if not keys:
                raise Untranslatable(f'{src.name}: RemDup sin claves')
            up = self.job.stage(ins[0].source_stage)
            order = ', '.join(f'{k} {d}' for k, d in sort_keys(up)) if up and up.stage_type == 'PxSort' else ', '.join(keys)
            if not (up and up.stage_type == 'PxSort'):
                st.status = max_status(st.status, 'REVIEW')
                st.issues.append(f'{src.name}: sin Sort previo, «{src.properties.get("keep", "first")}» se toma por las claves')
            direction = '' if (src.properties.get('keep') or 'first') == 'first' else ' DESC'
            inner = self.select(ins[0].name, st, depth + 1)
            return (f"SELECT {', '.join(c['name'] for c in l.columns)}\nFROM (\n  SELECT src.*, ROW_NUMBER() OVER (PARTITION BY {', '.join(keys)} "
                    f"ORDER BY {order}{direction}) AS mf_rn\n  FROM (\n{indent(inner, pad * 2)}\n  ) src\n)\nWHERE mf_rn = 1")
        if t == 'PxFilter' and len(ins) == 1:
            cond = osh_value(src.properties.get('where'), 'where')
            if not cond:
                raise Untranslatable(f'{src.name}: Filter sin cláusula where')
            st.status = max_status(st.status, 'REVIEW')
            st.issues.append(f'{src.name}: WHERE tomado del Filter ({cond}); la salida de rechazo usa NOT')
            outs = [x for x in self.job.links if x.source_stage == src.name]
            neg = outs.index(l) > 0 and src.properties.get('reject') == 'reject'
            inner = self.select(ins[0].name, st, depth + 1)
            return f"SELECT {self.cols(l)}\nFROM (\n{indent(inner, pad)}\n) src\nWHERE {'NOT ' if neg else ''}({self.jinja(cond)})"
        if t == 'PxJoin' and len(ins) == 2:
            keys = [v for k, v in osh(src.properties.get('key')) if k == 'key']
            kind = {'innerjoin': 'JOIN', 'leftouterjoin': 'LEFT JOIN', 'rightouterjoin': 'RIGHT JOIN', 'fullouterjoin': 'FULL JOIN'}.get(src.properties.get('operator'))
            if not keys or not kind:
                raise Untranslatable(f'{src.name}: Join sin claves o con operador {src.properties.get("operator")}')
            left_cols = {c['name'] for c in ins[0].columns}
            sel = ', '.join(c['name'] if c['name'] in keys else f"{'l' if c['name'] in left_cols else 'r'}.{c['name']}" for c in l.columns)
            st.status = max_status(st.status, 'REVIEW')
            st.issues.append(f'{src.name}: {kind} por {", ".join(keys)} (izquierda = {ins[0].name})')
            a, b = self.select(ins[0].name, st, depth + 1), self.select(ins[1].name, st, depth + 1)
            return f"SELECT {sel}\nFROM (\n{indent(a, pad)}\n) l\n{kind} (\n{indent(b, pad)}\n) r USING ({', '.join(keys)})"
        raise Untranslatable(f'{src.name}: {t} sin traducción verificada a SQL ({len(ins)} entrada(s))')

    def steps(self) -> list[Step]:
        out = []
        for s in self.job.stages:  # a stage type nobody maps fails explicitly wherever it sits in the job
            if s.stage_type not in COMPOSABLE:
                out.append(Step(name=f'manual_{ident(s.name)}', kind='sql', stage=s.name, status='MANUAL',
                                issues=[f'{s.name}: tipo {s.stage_type} sin equivalente verificado; rediseñar'],
                                sql=f'-- MF-MANUAL: {s.name} ({s.stage_type}) sin equivalente verificado\n'))
        for s in self.job.stages:
            ins = [l for l in self.job.links if l.target_stage == s.name]
            if not ins or s.stage_type not in ('OracleConnectorPX', 'PxDataSet', 'PxSequentialFile', 'PxPeek'):
                continue
            if s.stage_type == 'PxPeek':
                out.append(Step(name=f'skip_{ident(s.name)}', kind='skip', stage=s.name, issues=['Peek solo escribía al log']))
                continue
            for l in ins:
                st = Step(name=ident(s.name) + (f'_{ident(l.name)}' if len(ins) > 1 else ''), kind='sql', stage=s.name, columns=[c['name'] for c in l.columns],
                          keys=[c['name'] for c in l.columns if c.get('key')])
                try:
                    body = self.select(l.name, st)
                    st.sql = self.writer(s, l, body, st)
                except Untranslatable as e:
                    st.status, st.issues = 'MANUAL', st.issues + [str(e)]
                    st.sql = f'-- MF-MANUAL: {e}\n-- La etapa no tiene traducción verificada; esta tarea falla hasta completar la lógica.\n'
                out.append(st)
        for st in out:
            if self.jinja.issues:
                st.issues += [i for i in self.jinja.issues if i not in st.issues]
            if any(i.startswith('referencia a credencial') for i in st.issues):
                st.status = 'MANUAL'
        return self.loads + out

    def writer(self, s, l, body, st) -> str:
        cols = [c['name'] for c in l.columns]
        if s.stage_type == 'PxDataSet':
            table = self.stg(l.properties.get('in.dataset', s.name), l.columns)
            st.target = table
            return f"TRUNCATE TABLE {table};\nINSERT INTO {table} ({', '.join(cols)})\n{body};\n"
        if s.stage_type == 'PxSequentialFile':
            path = osh_value(l.properties.get('in.file'), 'file')
            st.kind, st.status, st.resource = 'file_export', max_status(st.status, 'REVIEW'), self.jinja(path) if path else None
            st.issues.append('formato de salida del archivo en config/files.yaml')
            return body + '\n'
        p = s.properties
        mode, table = p.get('Usage/WriteMode'), p.get('Usage/TableName')
        if mode == '8':
            raise Untranslatable(f'{s.name}: WriteMode 8 (bloque PL/SQL por fila): convertir a una sentencia por conjunto')
        if mode not in WRITE_MODES or not table or p.get('Usage/GenerateSQL') != '1':
            user = p.get('Usage/SQL/InsertStatement') or p.get('Usage/SQL/UpdateStatement')
            raise Untranslatable(f'{s.name}: WriteMode {mode} sin SQL generable' + (' (tiene SQL de usuario por fila)' if user else ''))
        table = self.jinja(table)
        st.target = table
        if p.get('Usage/TableAction', '0') != '0':
            st.status = max_status(st.status, 'REVIEW')
            st.issues.append(f"{s.name}: TableAction {p['Usage/TableAction']} (crear/reemplazar/truncar la tabla) no se replica: revisar")
        if WRITE_MODES[mode] == 'INSERT':
            return f"INSERT INTO {table} ({', '.join(cols)})\n{body};\n"
        keys = [c['name'] for c in l.columns if c.get('key')]
        if not keys:
            raise Untranslatable(f'{s.name}: {WRITE_MODES[mode]} sin columnas clave en el enlace')
        on = ' AND '.join(f't.{k} = s.{k}' for k in keys)
        if WRITE_MODES[mode] == 'UPDATE':
            sets = ', '.join(f't.{c} = s.{c}' for c in cols if c not in keys)
            return f'MERGE INTO {table} t\nUSING (\n{indent(body, "  ")}\n) s\nON ({on})\nWHEN MATCHED THEN UPDATE SET {sets};\n'
        return f"DELETE FROM {table} t\nWHERE EXISTS (\n  SELECT 1 FROM (\n{indent(body, '    ')}\n  ) s WHERE {on}\n);\n"


def row_order(job, link):
    """Row order a stage sees on `link`: the keys of an upstream Sort (through pass-through stages), or the file's line
    order (MF_SEQ, added when the file is loaded). None when DataStage's order is not reproducible in SQL."""
    seen = set()
    while link and link.name not in seen:
        seen.add(link.name)
        up = job.stage(link.source_stage)
        if up is None:
            return None
        if up.stage_type == 'PxSort':
            keys = sort_keys(up)
            return ', '.join(f'{k} {d}' for k, d in keys) or None
        if up.stage_type == 'PxSequentialFile':
            return 'MF_SEQ'
        if up.stage_type in ('PxCopy', 'PxPeek') and len(up.input_links) == 1:
            link = next(l for l in job.links if l.name == up.input_links[0])
            continue
        return None
    return None


def sort_keys(stage):
    pairs, out = osh(stage.properties.get('key')), []
    for i, (k, v) in enumerate(pairs):
        if k == 'key':
            nxt = pairs[i + 1] if i + 1 < len(pairs) else ('', '')
            out.append((v, 'DESC' if nxt[0] == 'asc\\desc' and nxt[1] == 'desc' else 'ASC'))
    return out


def indent(text, pad):
    return '\n'.join(pad + line if line else line for line in text.split('\n'))


def max_status(a, b):
    order = ('OK', 'REVIEW', 'MANUAL')
    return a if order.index(a) >= order.index(b) else b


SQL_TYPES = {'1': 'CHAR', '12': 'VARCHAR2', '-9': 'NVARCHAR2', '3': 'NUMBER', '2': 'NUMBER', '4': 'NUMBER(10)', '-5': 'NUMBER(19)',
             '5': 'NUMBER(5)', '8': 'BINARY_DOUBLE', '6': 'BINARY_DOUBLE', '7': 'BINARY_FLOAT', '9': 'DATE', '91': 'DATE',
             '93': 'TIMESTAMP', '11': 'TIMESTAMP', '-1': 'CLOB', '-4': 'BLOB'}


def ddl(table, columns) -> str:
    """Staging DDL from the link metadata. Precision/scale are not in the IR columns: VARCHAR2 gets a review marker."""
    lines = []
    for c in columns:
        t = SQL_TYPES.get(str(c.get('sqlType')), 'VARCHAR2(4000) /* tipo DataStage ' + str(c.get('sqlType')) + ': revisar */')
        if t in ('VARCHAR2', 'NVARCHAR2', 'CHAR'):
            t += '(4000 CHAR)'
        lines.append(f"  {c['name']} {t}{'' if c.get('nullable', True) else ' NOT NULL'}")
    return f'CREATE TABLE {table} (\n' + ',\n'.join(lines) + '\n);\n'

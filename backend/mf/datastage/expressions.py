"""Deterministic translation of DataStage parallel expressions (Transformer derivations, constraints, stage variables)
to Oracle SQL. A small tokenizer + recursive-descent parser over the subset BIBES uses; anything outside the subset
raises Untranslatable, and the caller keeps the stage as PARTIAL/MANUAL_REVIEW. Never guessed: an AI fallback may
propose SQL later, but it must pass `translate()` of its own output through the same validators (doc 58)."""
from __future__ import annotations

import re

_TOK = re.compile(r'\s*(?:(\d+\.\d+|\d+)|("(?:[^"\\]|\\.)*"|\'[^\']*\')|([A-Za-z_][\w$]*(?:\.[\w$]+)*)|(<>|<=|>=|=|<|>|\(|\)|,|\+|-|\*|/|:))')
FUNCS = {  # DataStage function -> (Oracle template, arity); {0},{1}.. are the translated arguments
    'currenttimestamp': ('SYSTIMESTAMP', 0), 'currentdate': ('TRUNC(SYSDATE)', 0), 'setnull': ('NULL', 0),
    'trim': ('TRIM({0})', 1), 'upcase': ('UPPER({0})', 1), 'downcase': ('LOWER({0})', 1), 'len': ('LENGTH({0})', 1),
    'nulltovalue': ('NVL({0}, {1})', 2), 'nulltozero': ('NVL({0}, 0)', 1), 'nulltoempty': ("NVL({0}, '')", 1),
    'isnull': ('({0} IS NULL)', 1), 'isnotnull': ('({0} IS NOT NULL)', 1),
}
KEYWORDS = {'if', 'then', 'else', 'and', 'or', 'not'}


class Untranslatable(Exception):
    pass


def _tokens(expr: str):
    pos, out = 0, []
    expr = expr.strip()
    while pos < len(expr):
        m = _TOK.match(expr, pos)
        if not m or m.end() == pos:
            raise Untranslatable(f'símbolo no soportado en «{expr[pos:pos + 20]}»')
        num, string, ident, op = m.groups()
        out.append(('num', num) if num else ('str', string) if string else ('id', ident) if ident else ('op', op))
        pos = m.end()
    return out


class _P:
    def __init__(self, toks, columns, params):
        self.t, self.i, self.columns, self.params = toks, 0, columns, params

    def peek(self, kind=None, value=None):
        if self.i >= len(self.t):
            return None
        k, v = self.t[self.i]
        if (kind and k != kind) or (value is not None and v.lower() != value):
            return None
        return v

    def take(self, kind=None, value=None):
        v = self.peek(kind, value)
        if v is None:
            raise Untranslatable(f"se esperaba {value or kind} en la posición {self.i}")
        self.i += 1
        return v

    def expr(self):
        if self.peek('id', 'if'):
            self.take('id', 'if')
            c = self.expr()
            self.take('id', 'then')
            a = self.expr()
            self.take('id', 'else')
            b = self.expr()
            return f'CASE WHEN {c} THEN {a} ELSE {b} END'
        return self.orx()

    def orx(self):
        left = self.andx()
        while self.peek('id', 'or'):
            self.take()
            left = f'({left} OR {self.andx()})'
        return left

    def andx(self):
        left = self.notx()
        while self.peek('id', 'and'):
            self.take()
            left = f'({left} AND {self.notx()})'
        return left

    def notx(self):
        if self.peek('id', 'not'):
            self.take()
            return f'NOT ({self.notx()})'
        return self.cmp()

    def cmp(self):
        left = self.concat()
        for op in ('<>', '<=', '>=', '=', '<', '>'):
            if self.peek('op', op):
                self.take()
                right = self.concat()
                if right == 'NULL':  # DataStage `x = SetNull()` compares with null
                    return f'({left} IS {"NOT " if op == "<>" else ""}NULL)'
                return f'({left} {op} {right})'
        return left

    def concat(self):
        left = self.add()
        while self.peek('op', ':'):
            self.take()
            left = f'{left} || {self.add()}'
        return left

    def add(self):
        left = self.mul()
        while self.peek('op', '+') or self.peek('op', '-'):
            op = self.take()
            left = f'{left} {op} {self.mul()}'
        return left

    def mul(self):
        left = self.atom()
        while self.peek('op', '*') or self.peek('op', '/'):
            op = self.take()
            left = f'{left} {op} {self.atom()}'
        return left

    def atom(self):
        if self.peek('op', '('):
            self.take()
            e = self.expr()
            self.take('op', ')')
            return f'({e})'
        if self.peek('op', '-'):
            self.take()
            return f'-{self.atom()}'
        if self.peek('num'):
            return self.take()
        if self.peek('str'):
            s = self.take()[1:-1].replace('\\"', '"')
            return "'" + s.replace("'", "''") + "'"
        name = self.take('id')
        if name.lower() in KEYWORDS:
            raise Untranslatable(f'palabra reservada fuera de lugar: {name}')
        if self.peek('op', '('):
            self.take()
            args = []
            if not self.peek('op', ')'):
                args.append(self.expr())
                while self.peek('op', ','):
                    self.take()
                    args.append(self.expr())
            self.take('op', ')')
            f = FUNCS.get(name.lower())
            if f is None:
                raise Untranslatable(f'función sin equivalente verificado: {name}()')
            if len(args) != f[1]:
                raise Untranslatable(f'{name}() con {len(args)} argumento(s)')
            return f[0].format(*args)
        return self.ref(name)

    def ref(self, name):
        if name in self.columns:
            return self.columns[name]
        if '.' in name:  # Link.Column
            link, col = name.rsplit('.', 1)
            if link in self.params:  # parameter set: P_SET.$P_X / P_SET.X
                return self.params[link](col)
            if self.columns.get('*'):
                return f'{self.columns["*"]}.{col}'
            raise Untranslatable(f'referencia no resuelta: {name}')
        if name in self.params:
            return self.params[name](None)
        raise Untranslatable(f'identificador no resuelto: {name}')


def translate(expr: str, columns: dict | None = None, params: dict | None = None) -> str:
    """columns: DataStage name -> SQL (stage variables, `*` = alias for Link.Column refs); params: name -> fn(member)."""
    if expr is None or not expr.strip():
        raise Untranslatable('expresión vacía')
    p = _P(_tokens(expr), columns or {}, params or {})
    out = p.expr()
    if p.i != len(p.t):
        raise Untranslatable(f'texto sobrante a partir de la posición {p.i}')
    return out


def _mentions(name, expr):
    return re.search(r'(?<![\w.$])' + re.escape(name) + r'(?!\w)', expr or '') is not None


def stage_vars_sql(stage_vars, params, order_by: str | None, alias='src') -> tuple[dict, bool]:
    """Transformer stage variables -> {name: SQL}. DataStage evaluates them top-down once per row, so a reference to a
    variable defined LATER (or to itself) reads its value from the PREVIOUS row: LAG(<its SQL>) OVER (ORDER BY order_by).
    Without a known row order, or when the state accumulates (LAG over a LAG), it is Untranslatable. Returns (sql, used_lag)."""
    names = [v['name'] for v in stage_vars]
    idx = {n: i for i, n in enumerate(names)}
    cur, used = {}, [False]

    def compute(n, stack):
        if n in cur:
            return cur[n]
        if n in stack:
            raise Untranslatable(f'estado acumulado entre filas en {n}')
        i, expr = idx[n], stage_vars[idx[n]]['expression'] or ''
        refs = {'*': alias}
        for m in names:
            if not _mentions(m, expr):
                continue
            if idx[m] < i:
                refs[m] = f'({compute(m, stack | {n})})'
                continue
            if order_by is None:
                raise Untranslatable(f'la variable {n} lee el valor de la fila anterior ({m}) y no hay un orden de filas definido')
            prev = compute(m, stack | {n}) if m != n else None
            if prev is None or 'LAG(' in prev:
                raise Untranslatable(f'estado acumulado entre filas en {n}')
            init = stage_vars[idx[m]].get('initial')
            default = '' if not init or init.strip().lower() in ('setnull()', '') else f', 1, {translate(init, {}, params)}'
            refs[m] = f'LAG({prev}{default}) OVER (ORDER BY {order_by})'
            used[0] = True
        cur[n] = translate(expr, refs, params)
        return cur[n]
    for n in names:
        compute(n, frozenset())
    return cur, used[0]


if __name__ == '__main__':  # self-check on the BIBES derivation shapes
    cols = {'*': 'src'}
    assert translate('CurrentTimestamp()') == 'SYSTIMESTAMP'
    assert translate('LK_A.ACCT_ID', cols) == 'src.ACCT_ID'
    assert translate('FLAGM = "I"', {'FLAGM': 'src.FLAGM'}) == "(src.FLAGM = 'I')"
    assert translate('If isNull(Id) Then setNull() Else Id', {'Id': 'Id'}) == 'CASE WHEN (Id IS NULL) THEN NULL ELSE Id END'
    assert translate('P_C.$P_USR', params={'P_C': lambda m: f"'{{{{ var.value.P_C__{m.lstrip('$')} }}}}'"}) == "'{{ var.value.P_C__P_USR }}'"
    svs = [{'name': 'IdAnterior', 'expression': 'If isNull(Id) Then setNull() Else Id', 'initial': 'SetNull()'},
           {'name': 'Id', 'expression': 'LK.LOG_ID'}, {'name': 'FLAGM', 'expression': 'if Id <> IdAnterior then "I" else "R"'}]
    sql, lag = stage_vars_sql(svs, {}, 'LOG_ID ASC')
    assert lag and sql['IdAnterior'] == ('CASE WHEN (LAG(src.LOG_ID) OVER (ORDER BY LOG_ID ASC) IS NULL) THEN NULL '
                                         'ELSE LAG(src.LOG_ID) OVER (ORDER BY LOG_ID ASC) END')
    assert sql['FLAGM'].startswith("CASE WHEN ((src.LOG_ID) <> (CASE WHEN") and sql['FLAGM'].endswith("THEN 'I' ELSE 'R' END")
    for args in ((svs, {}, None), ([{'name': 'S', 'expression': 'S + 1'}], {}, 'X')):
        try:
            stage_vars_sql(*args)
            raise AssertionError(args)
        except Untranslatable:
            pass
    for bad in ('Generate(REJECT_REASON)', 'f.DecIT(x)', 'A = '):
        try:
            translate(bad, cols)
            raise AssertionError(bad)
        except Untranslatable:
            pass
    print('ok')

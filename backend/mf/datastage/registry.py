"""DataStageAirflowRegistry (doc 54): one decision per stage, from the stage's own evidence (properties, links,
expressions), never from its type alone. Separate from the Camel registries (no shared tables or rules)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .expressions import Untranslatable, stage_vars_sql, translate

STATES = ('SUPPORTED', 'SEMANTIC_MAPPING', 'CONDITIONAL', 'REQUIRES_SQL_REWRITE', 'REQUIRES_PYTHON_REWRITE',
          'ARCHITECTURAL_REDESIGN', 'MANUAL_REVIEW', 'UNSUPPORTED')
# Oracle Connector WriteMode codes. Only the ones generated here are named; the rest stay a raw code for review.
WRITE_MODES = {'0': 'INSERT', '1': 'UPDATE', '2': 'DELETE'}


@dataclass
class StageDecision:
    job: str
    stage: str
    stage_type: str
    target: str
    status: str
    confidence: str          # HIGH | MEDIUM | LOW (a level, never a percentage)
    reason: str
    transformer: str | None = None           # TRANSLATED_SQL | TRANSLATED_PYTHON | PARTIAL | MANUAL_REVIEW
    untranslated: list = field(default_factory=list)
    manual_actions: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


def param_resolver(job):
    """Parameter references in expressions -> Jinja: parameter sets are environment config (Airflow Variables),
    connection members never reach SQL text (doc 59), plain job parameters are DAG params."""
    sets = {p.name for p in job.parameters if p.is_set}
    plain = {p.name for p in job.parameters if not p.is_set}
    res = {}
    for s in sets:
        res[s] = lambda m, s=s: f"'{{{{ var.value.{s}__{(m or '').lstrip('$')} }}}}'"
    for p in plain:
        res[p] = lambda m, p=p: f"'{{{{ params.{p} }}}}'"
    return res


def _transformer(job, stage, decision):
    links = [l for l in job.links if l.source_stage == stage.name]
    params = param_resolver(job)
    stage_cols, bad, lag = {'*': 'src'}, [], False
    try:  # the row order is resolved by the SQL composer; here only whether a translation exists
        svs, lag = stage_vars_sql(stage.stage_vars, params, 'MF_ORDER')
        stage_cols |= {n: f'src.MF_SV_{n}' for n in svs}
    except Untranslatable as e:
        bad.append(f'variables de etapa: {e}')
        stage_cols |= {v['name']: v['name'] for v in stage.stage_vars}
    for link in links:
        for col, der in link.expressions.items():
            try:
                translate(der, stage_cols, params)
            except Untranslatable as e:
                bad.append(f'{link.name}.{col}: {e}')
        if link.constraint:
            try:
                translate(link.constraint, stage_cols, params)
            except Untranslatable as e:
                bad.append(f'{link.name} (restricción): {e}')
    total = sum(len(l.expressions) + bool(l.constraint) for l in links) + len(stage.stage_vars)
    if not bad:
        decision.update(target='SQL (SELECT con expresiones)', status='SEMANTIC_MAPPING', confidence='MEDIUM' if lag else 'HIGH',
                        transformer='TRANSLATED_SQL', manual_actions=[], reason=f'{total} expresión(es) traducidas a SQL de Oracle de forma determinística'
                        + ('; el valor de la fila anterior se traduce a LAG() y requiere un orden de filas definido' if lag else ''))
    elif len(bad) < total:
        decision.update(target='SQL + revisión', status='REQUIRES_SQL_REWRITE', confidence='MEDIUM', transformer='PARTIAL',
                        reason=f'{total - len(bad)} de {total} expresiones traducidas', untranslated=bad,
                        manual_actions=[f'{stage.name}: completar {len(bad)} expresión(es) sin equivalente verificado'])
    else:
        decision.update(target='manual', status='MANUAL_REVIEW', confidence='LOW', transformer='MANUAL_REVIEW',
                        reason='ninguna expresión tiene equivalente verificado', untranslated=bad,
                        manual_actions=[f'{stage.name}: reescribir la lógica del Transformer'])


def re_word(name, text):
    import re
    return re.search(r'(?<![\w.$])' + re.escape(name) + r'(?![\w])', text) is not None


def decide(job, stage) -> StageDecision:
    t, p = stage.stage_type, stage.properties
    d = {'target': '', 'status': 'UNSUPPORTED', 'confidence': 'LOW', 'reason': f'tipo de etapa sin mapeo: {t}', 'transformer': None,
         'untranslated': [], 'manual_actions': [f'{stage.name}: tipo {t} sin equivalente; rediseñar']}
    ins = [l for l in job.links if l.target_stage == stage.name]
    outs = [l for l in job.links if l.source_stage == stage.name]
    if t == 'CJobActivity':
        d.update(target='dependencia de tarea (TaskGroup del job)', status='SUPPORTED', confidence='HIGH', manual_actions=[],
                 reason=f"ejecuta {p.get('Jobname')}: tarea del job dentro del DAG de la secuencia")
    elif t == 'CCondition':
        exprs = [l.trigger.get('expression') for l in outs if l.trigger and l.trigger.get('type') == '6']
        try:
            for e in exprs:  # ACTIVITY.var reads XCom, a bare name is a sequence parameter
                translate(e, {'*': 'xcom'}, param_resolver(job))
            d.update(target='BranchPythonOperator', status='CONDITIONAL', confidence='MEDIUM', manual_actions=[],
                     reason=f'{len(exprs)} condición(es) personalizadas evaluadas sobre XCom de las variables de usuario')
        except Untranslatable as e:
            d.update(target='BranchPythonOperator + revisión', status='MANUAL_REVIEW', confidence='LOW', reason=str(e),
                     manual_actions=[f'{stage.name}: traducir la condición «{exprs}»'])
    elif t == 'CExecCommandActivity':
        d.update(target='BashOperator', status='CONDITIONAL', confidence='MEDIUM',
                 reason='comando de shell conservado como plantilla; nunca se ejecutó durante el análisis',
                 manual_actions=[f'{stage.name}: validar el comando y sus rutas en el worker de Airflow (no se ejecutó)'])
    elif t == 'CTerminatorActivity':
        d.update(target='tarea que falla (AirflowFailException)', status='SUPPORTED', confidence='HIGH', manual_actions=[],
                 reason=f"termina la secuencia con «{p.get('FinalMessage') or ''}»")
    elif t == 'CExceptionHandler':
        d.update(target='trigger_rule one_failed + on_failure_callback', status='SEMANTIC_MAPPING', confidence='MEDIUM', manual_actions=[],
                 reason='el manejador de excepciones se activa cuando falla cualquier tarea previa')
    elif t == 'CUserVarsActivity':
        bad = [f'{k}: {v}' for k, v in (p.get('variables') or {}).items() if v and _routine(v)]
        if bad:
            d.update(target='PythonOperator + revisión', status='MANUAL_REVIEW', confidence='LOW', reason='usa rutinas de DataStage',
                     untranslated=bad, manual_actions=[f'{stage.name}: reemplazar la rutina ({b})' for b in bad])
        else:
            d.update(target='PythonOperator (XCom)', status='CONDITIONAL', confidence='MEDIUM', manual_actions=[],
                     reason='variables de usuario calculadas en Python y publicadas por XCom')
    elif t == 'CSequencer':
        mode = 'all_success' if p.get('SequencerType') == '0' else 'one_success'
        d.update(target=f'trigger_rule={mode}', status='CONDITIONAL', confidence='MEDIUM', manual_actions=[],
                 reason=f"SequencerType {p.get('SequencerType')}: verificar el modo (todos/cualquiera)")
    elif t == 'OracleConnectorPX':
        sql = p.get('Usage/SQL/SelectStatement') or p.get('Usage/SQL/InsertStatement') or p.get('Usage/SQL/UpdateStatement')
        mode = p.get('Usage/WriteMode')
        if outs and sql:
            d.update(target='SQLExecuteQueryOperator (SELECT como CTE)', status='SUPPORTED', confidence='HIGH', manual_actions=[],
                     reason='lectura con SELECT de usuario: se conserva el SQL y se empuja a Oracle')
        elif outs and p.get('Usage/TableName'):
            d.update(target='SQLExecuteQueryOperator (SELECT de la tabla)', status='SUPPORTED', confidence='HIGH', manual_actions=[],
                     reason=f"lectura de {p.get('Usage/TableName')} con las columnas del enlace")
        elif ins and mode in WRITE_MODES and p.get('Usage/GenerateSQL') == '1' and p.get('Usage/TableName'):
            d.update(target=f'SQLExecuteQueryOperator ({WRITE_MODES[mode]} generado)', status='SUPPORTED', confidence='HIGH', manual_actions=[],
                     reason=f"{WRITE_MODES[mode]} sobre {p.get('Usage/TableName')} con las columnas del enlace")
        elif ins and mode == '8':
            d.update(target='bloque PL/SQL', status='REQUIRES_SQL_REWRITE', confidence='MEDIUM', reason='WriteMode 8 (PL/SQL) por fila',
                     manual_actions=[f'{stage.name}: convertir el bloque PL/SQL por fila en una sentencia por conjunto'])
        else:
            d.update(target='SQLExecuteQueryOperator + revisión', status='MANUAL_REVIEW', confidence='LOW',
                     reason=f'sin SQL ni tabla generable (WriteMode {mode}, GenerateSQL {p.get("Usage/GenerateSQL")})',
                     manual_actions=[f'{stage.name}: escribir la sentencia SQL'])
    elif t == 'PxDataSet':
        d.update(target='tabla de staging (STG_*)', status='SEMANTIC_MAPPING', confidence='HIGH', manual_actions=[],
                 reason='el DataSet temporal se vuelve una tabla de staging en Oracle (SQL pushdown)')
    elif t == 'PxSequentialFile':
        d.update(target='carga/descarga de archivo (Python)', status='REQUIRES_PYTHON_REWRITE', confidence='MEDIUM',
                 reason='archivo plano: lectura o escritura en Python desde el worker',
                 manual_actions=[f'{stage.name}: confirmar ruta, formato y delimitador del archivo en el destino'])
    elif t in ('PxFilter', 'PxSort', 'PxRemDup', 'PxJoin', 'PxMerge', 'PxCopy', 'PxPeek', 'PxColumnGenerator', 'PxRowGenerator'):
        _relational(t, p, stage, d, ins, outs)
    elif t == 'CTransformerStage':
        _transformer(job, stage, d)
    return StageDecision(job=job.name, stage=stage.name, stage_type=t, **d)


def _routine(expr):
    import re
    return re.search(r'(?<![\w])[A-Za-z]\w*\.[A-Za-z]\w*\(', expr) is not None


def _relational(t, p, stage, d, ins, outs):
    keys = p.get('key')
    if t == 'PxFilter' and p.get('where'):
        d.update(target='SQL WHERE', status='REQUIRES_SQL_REWRITE', confidence='MEDIUM', reason=f"where: {p['where']}",
                 manual_actions=[f'{stage.name}: verificar la cláusula WHERE traducida'])
    elif t == 'PxSort':
        d.update(target='ORDER BY / sin efecto en pushdown', status='SEMANTIC_MAPPING', confidence='HIGH', manual_actions=[],
                 reason='el orden solo importa para la etapa siguiente (RemDup/Merge); en SQL se resuelve con ORDER BY o ROW_NUMBER')
    elif t == 'PxRemDup' and keys:
        d.update(target='ROW_NUMBER() OVER (PARTITION BY claves)', status='SEMANTIC_MAPPING', confidence='MEDIUM', manual_actions=[],
                 reason=f"claves {keys}; conserva {p.get('keep') or 'first'}")
    elif t in ('PxJoin', 'PxMerge') and keys:  # Merge: master = first input pin, rejects = updates without master
        d.update(target='SQL JOIN' if t == 'PxJoin' else 'SQL JOIN (Merge maestro/actualización)', status='REQUIRES_SQL_REWRITE',
                 confidence='MEDIUM', reason=f'claves {keys}, {len(ins)} entradas',
                 manual_actions=[f'{stage.name}: confirmar tipo de join y tratamiento de no coincidentes'])
    elif t == 'PxCopy':
        d.update(target='fan-out (misma CTE a varias salidas)', status='SUPPORTED', confidence='HIGH', manual_actions=[],
                 reason=f'{len(outs)} salida(s)')
    elif t == 'PxPeek':
        d.update(target='sin equivalente (depuración)', status='SUPPORTED', confidence='HIGH', manual_actions=[],
                 reason='Peek solo escribe al log; se omite')
    elif t in ('PxColumnGenerator', 'PxRowGenerator'):
        d.update(target='proyección / generación en SQL', status='REQUIRES_SQL_REWRITE', confidence='LOW',
                 reason='genera columnas o filas sintéticas', manual_actions=[f'{stage.name}: definir los valores generados'])
    else:
        d.update(target='SQL', status='MANUAL_REVIEW', confidence='LOW', reason=f'{t} sin claves en la definición',
                 manual_actions=[f'{stage.name}: completar la lógica'])


class DataStageAirflowRegistry:
    def decide_job(self, job) -> list[StageDecision]:
        return [decide(job, s) for s in job.stages]

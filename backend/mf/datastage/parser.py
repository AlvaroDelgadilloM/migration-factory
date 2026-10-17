"""DSX parser (doc 52): a DSX export is a tree of `BEGIN <KIND> ... END <KIND>` blocks whose lines are `Key "value"`
(backslash escapes) or `Key =+=+=+=` ... `=+=+=+=` (multi-line). The tokenizer builds that tree; the IR builder reads
records by OLEType. Nothing in the file is evaluated. Unknown record types, stage types and malformed lines are kept as
warnings (PARSED_WITH_WARNINGS) instead of failing the export; a job that cannot be built makes the result PARTIAL."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .model import DataStageJob, DataStageLink, DataStageParameter, DataStageProject, DataStageStage, SourceEvidence

MULTI = '=+=+=+='
_LINE = re.compile(r'^\s*(\w+)\s+(.*)$')
PIN_TYPES = {'CCustomInput', 'CCustomOutput', 'CTrxInput', 'CTrxOutput', 'CJSActivityInput', 'CJSActivityOutput'}
STAGE_TYPES = {'CCustomStage', 'CTransformerStage', 'CJSJobActivity', 'CJSCondition', 'CJSTerminatorActivity',
               'CJSExceptionHandler', 'CJSExecCmdActivity', 'CJSUserVarsActivity', 'CJSSequencer', 'CJSRoutineActivity',
               'CJSWaitForFileActivity', 'CJSMailActivity', 'CJSStartLoop', 'CJSEndLoop', 'CJSNestedCondition'}
IGNORED_TYPES = {'CJobDefn', 'CContainerView', 'CAnnotation'}
SEQUENCE_TYPES = {t for t in STAGE_TYPES if t.startswith('CJS')}


class Block:
    __slots__ = ('kind', 'line', 'props', 'children', 'closed')

    def __init__(self, kind, line):
        self.kind, self.line, self.props, self.children, self.closed = kind, line, [], [], False   # props: [(key, value, line)]

    def get(self, key, default=None):
        return next((v for k, v, _ in self.props if k == key), default)

    def collection(self, key):
        """Sub-records that follow `key "CClass"` up to the next plain property (DataStage writes collections that way)."""
        out, current = [], None
        for item in self._ordered():
            if isinstance(item, Block):
                if current == key:
                    out.append(item)
            else:
                current = item[0]
        return out

    def _ordered(self):
        return sorted([*self.props, *self.children], key=lambda x: x.line if isinstance(x, Block) else x[2])


def _unquote(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        return re.sub(r'\\(.)', r'\1', raw[1:-1])
    return raw


def tokenize(text: str, warnings: list) -> Block:
    root, stack = Block('ROOT', 0), []
    lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    i, cur = 0, root
    while i < len(lines):
        line = lines[i]
        n = i + 1
        s = line.strip()
        i += 1
        if not s:
            continue
        if s.startswith('BEGIN '):
            b = Block(s[6:].strip(), n)
            cur.children.append(b)
            stack.append(cur)
            cur = b
            continue
        if s.startswith('END '):
            if not stack or s[4:].strip() != cur.kind:
                warnings.append(f'línea {n}: END {s[4:].strip()} sin BEGIN correspondiente')
                if not stack:
                    continue
            cur.closed = True
            cur = stack.pop()
            continue
        m = _LINE.match(line)
        if not m:
            warnings.append(f'línea {n}: no reconocida')
            continue
        key, rest = m[1], m[2]
        if rest.strip() == MULTI:
            body = []
            while i < len(lines) and lines[i].strip() != MULTI:
                body.append(lines[i])
                i += 1
            if i >= len(lines):
                warnings.append(f'línea {n}: valor multilínea de {key} sin cierre')
            i += 1
            cur.props.append((key, '\n'.join(body), n))
        else:
            cur.props.append((key, _unquote(rest), n))
    if stack:
        warnings.append(f'{len(stack)} bloque(s) sin END al final del archivo')
    return root


def xml_properties(raw: str, warnings: list, where: str) -> dict:
    """Oracle/connector XMLProperties -> flat {'Usage/SQL/SelectStatement': text, ...} (only leaves with text)."""
    body = re.sub(r'^\s*<\?xml[^>]*\?>', '', raw.strip())
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        warnings.append(f'{where}: XMLProperties ilegible ({e})')
        return {}
    out = {}

    def walk(e, path):
        text = (e.text or '').strip()
        p = f'{path}/{e.tag}' if path else e.tag
        if text:
            out[p] = text
        for k in e:
            walk(k, p)
    for child in root:
        walk(child, '')
    return out


SECRET_KEY = re.compile(r'(?i)(^|/)(password|pwd|passwd|secret)$')
REDACTED = '***REDACTED***'


def redact_props(props: dict, job, stage_name, source_file) -> dict:
    """A literal credential (not a #PARAM# reference) is replaced before it reaches the IR, reports or the AI context."""
    for k, v in list(props.items()):
        pointer = re.fullmatch(r'/[A-Za-z]\w*(/\w+)+', v or '') is not None  # e.g. "/Connection/Password": where, not what
        if isinstance(v, str) and SECRET_KEY.search(k) and v.strip() and '#' not in v and v != REDACTED and not pointer:
            props[k] = REDACTED
            job.warnings.append(f'SECRET_FINDING: {stage_name}.{k} tiene una credencial literal (redactada; va en una Airflow Connection)')
    return props


def _pairs(blocks, key='Name', value='Value') -> dict:
    return {b.get(key): b.get(value) for b in blocks if b.get(key) is not None}


def parse_text(text: str, source_file: str) -> tuple[list, dict, list]:
    """Returns (jobs, header, warnings) for one DSX file."""
    warnings = []
    root = tokenize(text, warnings)
    header = {k: v for k, v, _ in next((b for b in root.children if b.kind == 'HEADER'), Block('HEADER', 0)).props}
    others = sorted({b.kind for b in root.children} - {'HEADER', 'DSJOB'})
    if others:
        warnings.append(f"secciones no interpretadas (se conservan como inventario): {', '.join(others)}")
    jobs = []
    for jb in (b for b in root.children if b.kind == 'DSJOB'):
        try:
            job = build_job(jb, source_file)
            if not jb.closed:  # a truncated export: keep what was read, never as a complete job
                job.warnings.append('job truncado: falta END DSJOB')
                warnings.append(f'job {job.name}: truncado (falta END DSJOB)')
            jobs.append(job)
        except Exception as e:  # noqa: BLE001 - one malformed job must not hide the others (PARTIAL)
            warnings.append(f"job {jb.get('Identifier')}: no se pudo construir ({type(e).__name__}: {e})")
    return jobs, header, warnings


def build_job(jb: Block, source_file: str) -> DataStageJob:
    records = [b for b in jb.children if b.kind == 'DSRECORD']
    by_id = {r.get('Identifier'): r for r in records}
    defn = by_id.get('ROOT') or next(r for r in records if r.get('OLEType') == 'CJobDefn')
    job = DataStageJob(name=jb.get('Identifier') or defn.get('Name'), job_type='PARALLEL', source_file=source_file,
                       description=defn.get('Description') or '', line=jb.line)
    for p in defn.collection('Parameters'):
        job.parameters.append(DataStageParameter(name=p.get('Name'), param_type=p.get('ParamType', '0'), default=p.get('Default'),
                                                 prompt=p.get('Prompt'), is_set=p.get('ParamType') == '13'))
    oles = {r.get('OLEType') for r in records}
    if oles & SEQUENCE_TYPES:
        job.job_type = 'SEQUENCE'
    for t in sorted(oles - STAGE_TYPES - PIN_TYPES - IGNORED_TYPES - {None}):
        job.warnings.append(f'tipo de registro sin interpretar: {t}')
    stage_of_pin, pin_order, link_pins = {}, {}, {}
    for r in records:
        if r.get('OLEType') not in STAGE_TYPES:
            continue
        st = DataStageStage(name=r.get('Name'), stage_type=r.get('StageType') or r.get('OLEType'), record_type=r.get('OLEType'))
        st.evidence.append(SourceEvidence(source_file, job.name + '/' + st.name, st.record_type, 'StageType', st.stage_type, r.line))
        for k, v, _ in r.props:  # scalar properties of activities (Jobname, Path, FinalMessage, ConditionType...)
            if k not in ('Identifier', 'OLEType', 'Readonly', 'Name', 'NextID', 'InputPins', 'OutputPins', 'StageType') and not k.endswith('Pos'):
                st.properties[k] = v
        props = _pairs(r.collection('Properties'))
        xml = props.pop('XMLProperties', None)
        st.properties.update({k: v for k, v in props.items() if v is not None})
        if xml:
            st.properties.update(xml_properties(xml, job.warnings, f'{job.name}/{st.name}'))
        redact_props(st.properties, job, st.name, source_file)
        if st.record_type == 'CJSJobActivity':
            st.properties['parameterValues'] = {b.get('Name'): b.get('DisplayValue') or b.get('Description') for b in r.collection('ParameterValues')}
            st.evidence.append(SourceEvidence(source_file, job.name + '/' + st.name, st.record_type, 'Jobname', r.get('Jobname') or '', r.line))
        if st.record_type == 'CJSUserVarsActivity':
            st.properties['variables'] = {b.get('Name'): b.get('Description') for b in r.collection('Variables')}
        if st.record_type == 'CTransformerStage':
            st.stage_vars = [{'name': b.get('Name'), 'expression': b.get('Expression'), 'initial': b.get('InitialValue')}
                             for b in r.collection('StageVars')]
            st.properties.pop('StageVars', None)
        for side in ('InputPins', 'OutputPins'):
            for i, pin in enumerate(p for p in (r.get(side) or '').split('|') if p):
                stage_of_pin[pin] = st.name
                pin_order[pin] = i
        job.stages.append(st)
    for r in records:  # links: one per OUTPUT pin, to its partner's stage
        ole = r.get('OLEType')
        if ole not in ('CCustomOutput', 'CTrxOutput', 'CJSActivityOutput'):
            continue
        src = stage_of_pin.get(r.get('Identifier'))
        partner = (r.get('Partner') or '').split('|')
        dst_rec = by_id.get(partner[0]) if partner else None
        if src is None or dst_rec is None:
            job.warnings.append(f"enlace {r.get('Name')}: extremo no encontrado ({r.get('Partner')})")
            continue
        link = DataStageLink(name=r.get('Name'), source_stage=src, target_stage=dst_rec.get('Name'))
        cols = r.collection('Columns')
        link.columns = [{'name': c.get('Name'), 'derivation': c.get('Derivation'), 'key': c.get('KeyPosition', '0') != '0',
                         'nullable': c.get('Nullable') == '1', 'sqlType': c.get('SqlType')} for c in cols]
        link.expressions = {c.get('Name'): c.get('Derivation') for c in cols if c.get('Derivation')}
        link.properties = {k: v for k, v in _pairs(r.collection('Properties')).items() if v is not None}
        in_pin = by_id.get(partner[1]) if len(partner) > 1 else None
        if in_pin is not None:  # the reading side's pin carries the write properties (dataset, file, table)
            link.properties.update({f'in.{k}': v for k, v in _pairs(in_pin.collection('Properties')).items() if v is not None})
        if r.get('Constraint'):
            link.constraint = r.get('Constraint')
        if ole == 'CJSActivityOutput':
            link.trigger = {'type': r.get('ConditionType', '0'), 'expression': r.get('TriggerExpression')}
        job.links.append(link)
        link_pins[link.name] = (pin_order.get(r.get('Identifier'), 0), pin_order.get(partner[1], 0) if len(partner) > 1 else 0)
    for st in job.stages:  # pin order is meaningful (Merge master = first input, reject links after the main output)
        st.output_links = sorted((l.name for l in job.links if l.source_stage == st.name), key=lambda n: link_pins[n][0])
        st.input_links = sorted((l.name for l in job.links if l.target_stage == st.name), key=lambda n: link_pins[n][1])
    return job


def parse_files(paths: list[Path], name: str, root: Path | None = None) -> DataStageProject:
    project = DataStageProject(name=name, metadata={'files': {}, 'warnings': [], 'status': 'PARSED'})
    failed = 0
    for p in sorted(paths):
        rel = p.relative_to(root).as_posix() if root else p.name
        raw = p.read_bytes()
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError:
            text = raw.decode('cp1252', errors='replace')  # DataStage exports declare CharacterSet "CP1252"
        jobs, header, warnings = parse_text(text, rel)
        project.metadata['files'][rel] = {'header': header, 'jobs': [j.name for j in jobs], 'warnings': warnings}
        project.metadata['warnings'] += [f'{rel}: {w}' for w in warnings]
        failed += any(w.startswith('job ') for w in warnings) or not jobs
        project.jobs += jobs
    versions = sorted({f['header'].get('ServerVersion') for f in project.metadata['files'].values() if f['header'].get('ServerVersion')})
    project.metadata['dataStageVersion'] = ', '.join(versions) or None
    job_warnings = sum(len(j.warnings) for j in project.jobs)
    project.metadata['status'] = ('FAILED' if not project.jobs else 'PARTIAL' if failed
                                  else 'PARSED_WITH_WARNINGS' if project.metadata['warnings'] or job_warnings else 'PARSED')
    return project

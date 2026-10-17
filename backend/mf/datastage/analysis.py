"""Inventory, dependency graph + waves (doc 56) and complexity / migratability (doc 55), from the IR only."""
from __future__ import annotations

import re
from collections import defaultdict

from .registry import DataStageAirflowRegistry

WEIGHTS = {  # factor -> points per occurrence (the score is a sum of named factors, reported with each factor)
    'stage': 1, 'oracle': 2, 'transformer': 5, 'join_merge': 4, 'remdup_sort': 2, 'shell': 6, 'routine': 6,
    'temp_dataset': 1, 'branch': 3, 'parameter': 0.5, 'file': 2, 'error_handling': 2, 'untranslated_expression': 3,
    'cdc_scd': 10, 'plsql': 5,
}
LEVELS = ((15, 'LOW'), (35, 'MEDIUM'), (70, 'HIGH'))
MIGRATABILITY = ('AUTO_MIGRATABLE', 'SEMIAUTOMATIC', 'MANUAL_REVIEW', 'ARCHITECTURAL_REDESIGN')


def resources(job):
    """(reads, writes) of a parallel job: datasets, files and Oracle tables, as written in the DSX (parameters kept)."""
    reads, writes = set(), set()
    for l in job.links:
        src, dst = job.stage(l.source_stage), job.stage(l.target_stage)
        for key in ('dataset', 'file'):
            if src and src.stage_type in ('PxDataSet', 'PxSequentialFile') and l.properties.get(key):
                reads.add(f'{key}:{l.properties[key]}')
            if dst and dst.stage_type in ('PxDataSet', 'PxSequentialFile') and l.properties.get(f'in.{key}'):
                writes.add(f'{key}:{l.properties[f"in.{key}"]}')
    for s in job.stages:
        if s.stage_type != 'OracleConnectorPX':
            continue
        if s.output_links:
            for k, v in s.properties.items():
                if k.startswith('Usage/SQL/SelectStatement/Tables/Table'):
                    reads.add(f'table:{v}')
        if s.input_links and s.properties.get('Usage/TableName'):
            writes.add(f"table:{s.properties['Usage/TableName']}")
    return reads, writes


def _scoped(res, job):
    # ponytail: a name built from parameters (#P_X#) only matches inside the same export (flow), where the parameter
    # values are the same; literal names match across exports. Upgrade: resolve parameter sets per environment.
    return f'{job.source_file}::{res}' if '#' in res else res


def dependency_graph(project):
    edges, reasons = defaultdict(set), defaultdict(list)

    def add(a, b, why):
        if a != b and a in names and b in names:
            edges[a].add(b)
            reasons[(a, b)].append(why)
    names = {j.name for j in project.jobs}
    for seq in project.sequences:
        acts = {s.name: s for s in seq.stages}
        for s in seq.stages:
            if s.stage_type == 'CJobActivity' and s.properties.get('Jobname'):
                add(s.properties['Jobname'], seq.name, 'secuencia → JobActivity')
        for l in seq.links:  # predecessor/successor chaining between activities
            a, b = acts.get(l.source_stage), acts.get(l.target_stage)
            if a and b and a.stage_type == b.stage_type == 'CJobActivity':
                add(a.properties.get('Jobname'), b.properties.get('Jobname'), f'encadenamiento en {seq.name}')
    producers = defaultdict(set)
    io = {j.name: resources(j) for j in project.jobs if j.job_type == 'PARALLEL'}
    for j in project.jobs:
        for w in io.get(j.name, ((), ()))[1]:
            producers[_scoped(w, j)].add(j.name)
    dropped = []
    for j in project.jobs:  # data edges after the explicit ones: one that contradicts the sequence order is dropped, reported
        for r in sorted(io.get(j.name, ((), ()))[0]):
            for p in sorted(producers.get(_scoped(r, j), ())):
                if p == j.name or j.name in edges.get(p, ()):
                    add(p, j.name, f'productor/consumidor: {r}')
                elif _reaches(edges, j.name, p):
                    dropped.append({'from': p, 'to': j.name, 'resource': r, 'reason': 'cerraría un ciclo con el orden explícito'})
                else:
                    add(p, j.name, f'productor/consumidor: {r}')
    return edges, reasons, io, dropped


def _reaches(edges, a, b):
    seen, todo = set(), [a]
    while todo:
        n = todo.pop()
        if n == b:
            return True
        if n not in seen:
            seen.add(n)
            todo += edges.get(n, ())
    return False


def waves(names, edges):
    """Topological levels (Kahn). Edges that close a cycle are dropped and reported, never silently ignored."""
    indeg = {n: 0 for n in names}
    for a in edges:
        for b in edges[a]:
            indeg[b] += 1
    level, out, done = [n for n in sorted(names) if indeg[n] == 0], [], set()
    while level:
        out.append(level)
        done |= set(level)
        nxt = []
        for a in level:
            for b in sorted(edges.get(a, ())):
                indeg[b] -= 1
                if indeg[b] == 0:
                    nxt.append(b)
        level = sorted(set(nxt))
    cyclic = sorted(set(names) - done)
    if cyclic:
        out.append(cyclic)
    return out, cyclic


def complexity(job, decisions):
    f = defaultdict(float)
    for s in job.stages:
        t = s.stage_type
        f['stage'] += 1
        f['oracle'] += t == 'OracleConnectorPX'
        f['transformer'] += t == 'CTransformerStage'
        f['join_merge'] += t in ('PxJoin', 'PxMerge')
        f['remdup_sort'] += t in ('PxRemDup', 'PxSort')
        f['shell'] += t == 'CExecCommandActivity'
        f['temp_dataset'] += t == 'PxDataSet'
        f['file'] += t == 'PxSequentialFile'
        f['branch'] += t in ('CCondition', 'CSequencer')
        f['error_handling'] += t in ('CExceptionHandler', 'CTerminatorActivity')
        f['cdc_scd'] += t in ('PxChangeCapture', 'PxChangeApply', 'PxSCD')
        f['plsql'] += s.properties.get('Usage/WriteMode') == '8'
        f['routine'] += sum(1 for v in (s.properties.get('variables') or {}).values() if v and re.search(r'\b[A-Za-z]\w*\.[A-Za-z]\w*\(', v))
    f['parameter'] = len(job.parameters)
    f['untranslated_expression'] = sum(len(d.untranslated) for d in decisions)
    factors = {k: v for k, v in f.items() if v}
    score = round(sum(WEIGHTS[k] * v for k, v in factors.items()))
    level = next((name for limit, name in LEVELS if score < limit), 'VERY_HIGH')
    statuses = {d.status for d in decisions}
    if statuses & {'UNSUPPORTED', 'ARCHITECTURAL_REDESIGN'}:
        mig = 'ARCHITECTURAL_REDESIGN'
    elif 'MANUAL_REVIEW' in statuses:
        mig = 'MANUAL_REVIEW'
    elif statuses - {'SUPPORTED'}:
        mig = 'SEMIAUTOMATIC'
    else:
        mig = 'AUTO_MIGRATABLE'
    confs = {d.confidence for d in decisions}
    conf = 'LOW' if 'LOW' in confs else 'MEDIUM' if 'MEDIUM' in confs else 'HIGH'
    reasons = [f'{k} ×{v:g} (+{WEIGHTS[k] * v:g})' for k, v in sorted(factors.items(), key=lambda kv: -WEIGHTS[kv[0]] * kv[1])]
    return {'job': job.name, 'jobType': job.job_type, 'score': score, 'complexity': level, 'migratability': mig,
            'confidence': conf, 'reasons': reasons, 'untranslated': int(f['untranslated_expression'])}


def analyze(project) -> dict:
    reg = DataStageAirflowRegistry()
    decisions = {j.name: reg.decide_job(j) for j in project.jobs}
    edges, reasons, io, dropped = dependency_graph(project)
    names = [j.name for j in project.jobs]
    wv, cyclic = waves(names, edges)
    jobs = []
    for j in project.jobs:
        c = complexity(j, decisions[j.name])
        r, w = io.get(j.name, (set(), set()))
        jobs.append(c | {'sourceFile': j.source_file, 'stages': len(j.stages), 'links': len(j.links), 'parameters': len(j.parameters),
                         'reads': sorted(r), 'writes': sorted(w),
                         'dependsOn': sorted(a for a in edges if j.name in edges[a]),
                         'calls': sorted(s.properties.get('Jobname') for s in j.stages if s.stage_type == 'CJobActivity'),
                         'manualActions': [m for d in decisions[j.name] for m in d.manual_actions]})
    called = {c for x in jobs for c in x['calls']}
    masters = [x['job'] for x in jobs if x['jobType'] == 'SEQUENCE' and x['job'] not in called]
    rank = {m: i for i, m in enumerate(('AUTO_MIGRATABLE', 'SEMIAUTOMATIC', 'MANUAL_REVIEW', 'ARCHITECTURAL_REDESIGN'))}
    pilot = {}  # doc 67: one LOW, one MEDIUM, one HIGH parallel job and one master sequence, the most migratable of each
    for level in ('LOW', 'MEDIUM', 'HIGH'):
        cands = [x for x in jobs if x['jobType'] == 'PARALLEL' and x['complexity'] == level]
        if cands:
            pilot[level] = min(cands, key=lambda x: (rank[x['migratability']], x['untranslated'], x['score'], x['job']))['job']
    seqs = [x for x in jobs if x['job'] in masters]
    if seqs:
        pilot['MASTER_SEQUENCE'] = min(seqs, key=lambda x: (rank[x['migratability']], x['score'], x['job']))['job']
    return {'jobs': jobs, 'decisions': {k: [d.to_dict() for d in v] for k, v in decisions.items()},
            'graph': {'edges': [{'from': a, 'to': b, 'reasons': reasons[(a, b)]} for a in sorted(edges) for b in sorted(edges[a])],
                      'waves': [{'wave': i, 'jobs': w} for i, w in enumerate(wv)], 'cycles': cyclic, 'droppedEdges': dropped, 'masterSequences': masters},
            'missingJobs': sorted(called - set(names)), 'pilot': pilot}

"""MODERNIZE -> REFACTOR -> BUILD -> TEST -> REPORT on an isolated copy of already-migrated code.

Each refactor is one batch: snapshot (commit) before, apply, `mvn clean test`, and automatic rollback to the snapshot if
the quality gate fails (build fails, tests fail, or fewer tests run than at baseline). ARCHITECTURE rules are never
applied; rules that change a public contract need an explicit --approve. The given project is never modified."""
import json
import shutil
import time
from pathlib import Path

from .. import ledger, rules as mrules
from ..security import redact
from . import detect, refactor

STATES = ('APPLIED', 'ROLLED_BACK', 'PROPOSED_ONLY', 'BLOCKED', 'NO_CHANGES')
ORDER = ('MOD_SAFE_CLEANUP', 'MOD_HARDCODED_CONFIG', 'MOD_SECRETS', 'MOD_FIELD_INJECTION')


class InvalidInput(ValueError):
    pass


def _runtime(root: Path, target: str | None) -> str:
    if target:
        return target
    poms = ' '.join(p.read_text(errors='replace') for p in root.rglob('pom.xml') if 'target' not in p.parts)
    return 'quarkus' if 'io.quarkus' in poms or 'camel-quarkus' in poms else 'spring'


def run(project: Path, out: Path, *, target: str | None = None, approved=(), auto_up_to: str = 'AUTO_TEST', log=print, ctx=None) -> dict:
    """ctx: the platform job context (cancellation, deadline, live log); the CLI creates its own."""
    from ..cli_tool import run_validation, write_json
    from ..sbom import checksums
    from ..worker import gitops, mavenops, runner, source as src_mod
    project, out = project.resolve(), out.resolve()
    if not project.is_dir() or not any(project.rglob('pom.xml')):
        raise InvalidInput(f'No hay un proyecto Maven en {project}')
    if out.exists() and any(out.iterdir()):
        raise InvalidInput(f'El directorio de salida no está vacío: {out}')
    if out == project or project in out.parents:
        raise InvalidInput('La salida debe estar fuera del proyecto')
    rules = detect.load_rules()
    (out / 'reports').mkdir(parents=True)
    ctx = ctx or runner.Context('cli', out, deadline=time.monotonic() + 4 * 3600, emit=None)
    work = out / '.work' / 'candidate'
    src_mod.copy_local(project, work)
    gitops.init_snapshot_repo(ctx, work)
    runtime = _runtime(work, target)
    # ANALYZE + BASELINE (the gate compares against this)
    before = detect.analyze(work, runtime)
    base_v, _ = run_validation(ctx, work, 'modernize-baseline', goals=('clean', 'test'))
    base_tests = (base_v.get('tests') or {}).get('tests', 0)
    baseline_ok = base_v['status'] == 'PASS'
    log(f"Modernización: baseline {base_v['status']} ({base_tests} pruebas)")
    quality_before = before['metrics'] | {'build': base_v['status'], 'tests': base_v.get('tests')}
    write_json(out / 'reports' / 'quality-before.json', quality_before)
    by_rule = {}
    for it in before['items']:
        by_rule.setdefault(it['rule'], []).append(it)
    factors = ledger.project_factors({'findings': [], 'testClasses': before['metrics']['testClasses']})
    results, last_gate = [], base_v

    def entry(r, status, reason='', **kw):
        e = {'rule': r['id'], 'tier': r['tier'], 'title': r['title'], 'confidence': r['level'],
             'confidenceMeaning': mrules.load()['levels'][r['level']], 'publicContract': r['publicContract'], 'mechanism': r['mechanism'],
             'status': status, 'reason': reason, 'rationale': [r['rationale']] + factors, 'items': by_rule.get(r['id'], [])} | kw
        results.append(e)
        log(f"{r['id']}: {status}" + (f' ({reason})' if reason else ''))
        return e

    for rid in ORDER:
        r = rules['byId'][rid]
        items = by_rule.get(rid, [])
        if rid == 'MOD_FIELD_INJECTION':
            items = [i for i in items if 'no transformable' not in i['detail']]
        if rid != 'MOD_SAFE_CLEANUP' and not items:
            continue
        if not baseline_ok:
            entry(r, 'BLOCKED', 'BASELINE_NOT_GREEN: sin build y pruebas en verde no se puede verificar un refactor')
            continue
        if (r['publicContract'] or mrules.needs_approval(r['level'], auto_up_to)) and rid not in approved:
            entry(r, 'PROPOSED_ONLY', ('cambia contratos públicos' if r['publicContract'] else f"nivel {r['level']} sobre el umbral {auto_up_to}")
                  + f': use --approve {rid}')
            continue
        prev = gitops.head(ctx, work)
        try:
            if rid == 'MOD_SAFE_CLEANUP':
                cfg = out / 'meta' / 'rewrite-modernize.yml'
                cfg.parent.mkdir(parents=True, exist_ok=True)
                cfg.write_text('---\n')
                res = mavenops.rewrite(ctx, work, 'run', plugin_version=rules['tooling']['pluginVersion'], artifacts=rules['tooling']['artifacts'],
                                       active=r['recipes'], config=cfg, name='modernize-safe')
                if res.exit_code != 0:
                    raise RuntimeError('la receta OpenRewrite terminó con error')
                applied = []
            elif rid == 'MOD_HARDCODED_CONFIG':
                applied = refactor.apply_config(work, before['httpEndpoints'], runtime)
            elif rid == 'MOD_SECRETS':
                applied = refactor.apply_secrets(work, [i for i in items], runtime)
            else:
                applied = refactor.apply_field_injection(work, sorted({i['file'] for i in items}))
        except (runner.Cancelled, runner.TimedOut):
            raise
        except Exception as exc:  # tooling failure: nothing kept
            gitops.reset_hard(ctx, work, prev)
            entry(r, 'ROLLED_BACK', f'TOOLING: {type(exc).__name__}: {str(exc)[:160]}')
            continue
        commit = gitops.commit_all(ctx, work, f'modernization: {rid}')
        if not commit:
            entry(r, 'NO_CHANGES', 'la regla no produjo cambios', edits=applied)
            continue
        patch = gitops.full_patch(ctx, work, prev, commit)
        gate, text = run_validation(ctx, work, f'modernize-gate-{rid}', goals=('clean', 'test'))
        ran = (gate.get('tests') or {}).get('tests', 0)
        if gate['status'] != 'PASS' or ran < base_tests:
            gitops.reset_hard(ctx, work, prev)  # automatic rollback to the snapshot taken before the batch
            from ..preflight import classify
            errs = classify(text)
            why = 'menos pruebas ejecutadas que en la línea base' if gate['status'] == 'PASS' else \
                (errs[0]['category'] + ': ' + errs[0]['message'][:160] if errs else 'quality gate FAIL')
            entry(r, 'ROLLED_BACK', why, gate=gate['status'], snapshotBefore=prev, patchText=patch)
            continue
        last_gate = gate
        entry(r, 'APPLIED', '', gate=gate['status'], tests=gate.get('tests'), snapshotBefore=prev, snapshotAfter=commit, patchText=patch,
              edits=applied)
    for r in rules['rules']:
        if r['id'] not in ORDER and by_rule.get(r['id']):
            entry(r, 'PROPOSED_ONLY', 'arquitectura: nunca se aplica automáticamente' if r['tier'] == 'ARCHITECTURE' else 'propuesta con código sugerido')
    blocked_inj = [i for i in by_rule.get('MOD_FIELD_INJECTION', []) if 'no transformable' in i['detail']]
    if blocked_inj:
        entry(rules['byId']['MOD_FIELD_INJECTION'] | {'id': 'MOD_FIELD_INJECTION'}, 'BLOCKED', 'casos que el transformador no cubre (ver detalle)',
              items=blocked_inj)
    # AFTER + REPORT
    after = detect.analyze(work, runtime)
    quality_after = after['metrics'] | {'build': last_gate['status'], 'tests': last_gate.get('tests')}
    write_json(out / 'reports' / 'quality-after.json', quality_after)
    for n, e in enumerate([e for e in results if e.get('patchText')], 1):
        name = f"refactors/{n:02d}-{e['rule']}.patch"
        (out / 'reports' / name).parent.mkdir(parents=True, exist_ok=True)
        (out / 'reports' / name).write_text(redact(e['patchText']))
        e['patch'] = name
        e['changes'] = [{'file': f['file'], 'lines': f"{h['newStart']}-{h['newStart'] + max(h['newLines'], 1) - 1}",
                         'before': ledger._excerpt(h['before']), 'after': ledger._excerpt(h['after'])}
                        for f in ledger.parse_patch(e.pop('patchText')) for h in f['hunks']]
        if e['status'] == 'APPLIED':
            e['rollback'] = {'available': True, 'command': f'git apply -R reports/{name}'}
    write_json(out / 'reports' / 'refactors-applied.json', results)
    (out / 'reports' / 'modernization-report.md').write_text(report_markdown(results, quality_before, quality_after, runtime))
    (out / 'reports' / 'manual-recommendations.md').write_text(recommendations_markdown(results))
    src_mod.copy_local(work, out / 'modernized-project')
    for d in ('meta', 'home', 'tmp', '.work'):
        shutil.rmtree(out / d, ignore_errors=True)
    for f in (out / 'logs').glob('*.log'):
        f.write_text(redact(f.read_text(errors='replace')))
    (out / 'reports' / 'checksums.sha256').write_text(checksums(out, ('reports', 'modernized-project')))
    return {'baseline': base_v['status'], 'results': [{k: e[k] for k in ('rule', 'status', 'reason')} for e in results],
            'details': results, 'qualityBefore': quality_before, 'qualityAfter': quality_after, 'runtime': runtime}


def _delta(b, a):
    keys = ['javaFiles', 'javaLines', 'classes', 'routeBuilders', 'routes', 'testClasses', 'maxConfigureLines', 'findings']
    rows = [(k, b.get(k), a.get(k)) for k in keys]
    rules = sorted(set(b.get('findingsByRule', {})) | set(a.get('findingsByRule', {})))
    rows += [(f'hallazgos {r}', b.get('findingsByRule', {}).get(r, 0), a.get('findingsByRule', {}).get(r, 0)) for r in rules]
    return rows


def report_markdown(results, qb, qa, runtime) -> str:
    t = lambda rows, h: '\n'.join(['| ' + ' | '.join(h) + ' |', '|' + '---|' * len(h)] +
                                   ['| ' + ' | '.join(str('' if c is None else c).replace('|', '\\|') for c in r) + ' |' for r in rows])
    lines = ['# Reporte de modernización', '', f'Runtime: {runtime}. Flujo: ANALYZE → MODERNIZE → REFACTOR → BUILD → TEST → REPORT.',
             'Cada refactor se aplica como un lote con snapshot previo y quality gate (`mvn clean test`, sin menos pruebas que la línea base);',
             'si el gate falla se revierte automáticamente. La confianza es un nivel, no un porcentaje.', '',
             f"Línea base: build {qb['build']} · pruebas {qb.get('tests') or 'n/a'} → después: build {qa['build']} · pruebas {qa.get('tests') or 'n/a'}", '',
             '## Refactors', '', t([(e['rule'], e['tier'], e['confidence'], e['status'], len(e['items']), e.get('reason', '')) for e in results],
                                  ['Regla', 'Tier', 'Confianza', 'Estado', 'Casos', 'Motivo']),
             '', '## Calidad antes / después', '', t(_delta(qb, qa), ['Métrica', 'Antes', 'Después']), '',
             'Detalle de cambios (antes/después por archivo y línea): refactors-applied.json · parches individuales en reports/refactors/.',
             'Propuestas y bloqueos: manual-recommendations.md.']
    return '\n'.join(lines) + '\n'


def recommendations_markdown(results) -> str:
    lines = ['# Recomendaciones manuales', '', 'No se aplicaron automáticamente: requieren decisión (arquitectura, contrato público o comportamiento).', '']
    pending = [e for e in results if e['status'] in ('PROPOSED_ONLY', 'BLOCKED', 'ROLLED_BACK')]
    if not pending:
        return '\n'.join(lines + ['Ninguna.']) + '\n'
    for e in pending:
        lines += [f"## {e['rule']} — {e['title']}", '', f"- Tier: {e['tier']} · confianza: {e['confidence']} · estado: {e['status']}",
                  f"- Motivo: {e.get('reason') or '—'}", f"- Fundamento: {e['rationale'][0]}", '']
        for i in e['items'][:40]:
            lines.append(f"- `{i['file']}:{i['line']}` {i['detail']}" + (f"\n  - Sugerencia: `{i['suggestion']}`" if i.get('suggestion') else ''))
        if len(e['items']) > 40:
            lines.append(f"- … {len(e['items']) - 40} más (refactors-applied.json)")
        lines.append('')
    return '\n'.join(lines) + '\n'

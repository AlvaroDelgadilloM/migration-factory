"""Odoo 12 -> 19 scenarios on real Odoo + OpenUpgrade (workflow odoo-e2e.yml), through the same functions the worker runs
(mf.odoo.jobs: analyze, migrate, port_code). Synthetic data only: Odoo's demo data and the synthetic modules in examples/.
    python odoo_chain.py scenario <name> <dump.sql> [--start 12.0] [--third-party N] [--oca module] [--table name=rows]
                         [--code addons_dir] [--same 'query on source ==> query on 19']
    python odoo_chain.py code <addons dir> [--sql file] [--expect-sql object=CATEGORY]
The database server and the seven odoo-hop-<version> runners are started by the workflow."""
import argparse
import sys
import tempfile
import time
import uuid
from pathlib import Path

from mf.odoo import chain, jobs as oj


def ctx():
    from mf.worker.runner import Context
    return Context(job_id='e2e', workdir=Path(tempfile.mkdtemp()), deadline=time.monotonic() + 5 * 3600)


def fetch(url):
    import httpx
    r = httpx.get(url, timeout=300, follow_redirects=True)
    r.raise_for_status()
    return r.content


def scenario(a):
    """Analysis -> the decisions an architect would take (uninstall what blocks, apply the detected data fixes) -> the hops ->
    reconciliation, with the checks of the scenario."""
    from mf.worker.runner import run
    from mf.odoo import code as oc
    c, om = ctx(), uuid.uuid4()
    names = frozenset(oc.modules(Path(a.code))) if a.code else frozenset()
    res = oj.analyze(c, run, Path(a.dump), om, print, names)
    rows, counts = res['rows'], res['counts']
    print(f"[{a.name}] Odoo {res['sourceVersion']} · {counts} · saltos {res['hops']} · correcciones {[f['id'] for f in res['fixes']]}")
    assert res['from'] == a.start, res['from']
    third = sorted(r['name'] for r in rows if r['origin'] == 'THIRD_PARTY')
    assert len(third) == a.third_party, third
    if a.oca:
        oca = {r['name']: r for r in rows if r['origin'] == 'OCA'}
        assert a.oca in oca and oca[a.oca]['outcome'] != 'BLOCK', oca
    block = sorted(r['name'] for r in rows if r['outcome'] == 'BLOCK')
    decisions = {n: {'action': 'uninstall', 'reason': 'e2e: sin versión 19'} for n in block}
    decisions |= {f"fix:{f['id']}": {'action': 'apply', 'reason': 'e2e'} for f in res['fixes']}
    print(f"[{a.name}] desinstalar: {block}")
    custom = oj.ported_addons(c, run, Path(a.code), om, print, res['from'])['paths'] if a.code else None
    if a.code:
        assert {r['name'] for r in rows if r['origin'] == 'CUSTOM'} == set(names), [(r['name'], r['origin']) for r in rows]
    before = {q: chain.query(res['sourceDb'])(q.split(' ==> ')[0]) for q in a.same or []}
    m = oj.migrate(c, res, decisions, om, print, lambda i, n: None, fetch, lambda: False, 3 * 3600, custom)
    for h in m['hops']:
        print(f"[{a.name}] {h['version']}: {h['state']} errors={h['errors']} base={h['baseVersion']}")
    if m['state'] != 'succeeded':
        print(m['hops'][-1]['logTail'][-8000:])
        sys.exit(1)
    db = m['database']
    assert chain.base_version(db).startswith('19.0'), chain.base_version(db)
    for r in m['reconciliation']:
        print(f"[{a.name}] {r['state']:15} {r['label']}: {r['before']} -> {r['after']}")
    diff = [r['check'] for r in m['reconciliation'] if r['state'] == 'DIFFERENT' and not r.get('info')]
    assert not diff, diff
    installed = {r['name'] for r in chain.installed_modules(db)}
    assert not set(block) & installed, set(block) & installed
    if a.oca:
        assert a.oca in installed, f'{a.oca} no quedó instalado en 19'
    for n in names:
        assert n in installed, f'{n} no quedó instalado en 19'
    for q in a.same or []:
        after = chain.query(db)(q.split(' ==> ')[1])
        print(f"[{a.name}] datos: {q.split(' ==> ')[0]} → {before[q]} · en 19: {after}")
        assert before[q] and before[q] != ('0',) and before[q] == after, (q, before[q], after)
    for spec in a.table or []:
        name, rows_expected = spec.split('=')
        got = chain.query(db)(f'select count(*) from {name}')
        assert got == (rows_expected,), (name, got)
        print(f"[{a.name}] tabla {name}: {got[0]} fila(s), conservada")
    chain.drop_db(db)
    chain.drop_db(res['sourceDb'])


def code(a):
    """Custom module code (and its external SQL): port + install check of each module on a new Odoo 19."""
    from mf.worker.runner import run
    om = uuid.uuid4()
    res = oj.port_code(ctx(), run, Path(a.addons), om, print, lambda: False, 3 * 3600, a.sql or [])
    print('base:', res['base']['state'], res['base']['modules'])
    for r in res['rows']:
        t = r['install'].get('tests') or {}
        print(f"{r['name']}: {r['counts']} · instalar en 19: {r['install']['state']} · pruebas: {t.get('total', 0) - t.get('failed', 0) - t.get('errors', 0)}/{t.get('total', 0)}")
        for line in (t.get('failures') or []) + [f'funcional: {x}' for x in t.get('smoke') or []]:
            print('    ' + line)
        for f in r['findings']:
            if f['status'] != 'AUTO':
                print(f"    {f['status']:6} {f['pattern']} {f['file']}:{f['line']}")
        if r['install']['state'] == 'failed':
            print('\n'.join(line for line in r['install']['logTail'].splitlines() if ' ERROR ' in line or 'Error' in line)[-1500:])
    for o in res['externalSql']:
        print(f"SQL {o['category']:12} {o['kind']:17} {o['schema']}.{o['name']} · en 19: {o.get('in19')}")
    print('\n'.join(res['portWarnings'][:20]))
    state = {r['name']: r['install']['state'] for r in res['rows']}
    assert res['base']['state'] == 'succeeded', res['base']['logTail'][-3000:]
    assert all(v == 'succeeded' for v in state.values()), state   # the OCA tool + the platform's rules: every module installs
    assert not [r['name'] for r in res['rows'] if r['counts']['MANUAL']], [(r['name'], r['counts']) for r in res['rows']]
    tests = {r['name']: r['install'].get('tests') for r in res['rows']}
    assert all(t and t['total'] and not t['failed'] and not t['errors'] for t in tests.values()), tests   # and they work: their tests
    assert all((o.get('in19') or {}).get('state') == 'ok' for o in res['externalSql']), [o for o in res['externalSql'] if o.get('in19', {}).get('state') != 'ok']
    got = {o['name'] or o['schema']: o['category'] for o in res['externalSql']}
    for spec in a.expect_sql or []:
        name, cat = spec.split('=')
        assert got.get(name) == cat, (name, got.get(name), cat)
    if a.flows:   # the business flows through the platform's path (Pruebas): runner, odoo shell, results
        fl = oj.run_flows(ctx(), Path(a.addons), res['addons19'], res['rows'], a.sql or [], Path(a.flows).read_text(), om, print,
                          lambda: False, 3 * 3600)
        for c in fl['checks'] or []:
            print(('OK   ' if c['ok'] else 'FALLA'), c['check'], '·', c.get('got') or c.get('error'))
        assert fl['stage'] == 'flows' and fl['checks'] and all(c['ok'] for c in fl['checks']), (fl['stage'], fl.get('logTail', '')[-3000:])


def port(a):
    """The example's modules ported to 19 (with its external SQL, like the platform) into a folder, for the business flows."""
    import shutil
    from mf.odoo import code as oc
    from mf.worker.runner import run
    src = Path(a.addons)
    ext = oj.external_sql(src, a.sql or [], None, print)
    res = oc.port_steps(src, ctx().workdir / 'ported', run, ctx(), print, ext=ext)
    shutil.rmtree(a.out, ignore_errors=True)
    for name, m in oc.modules(res['snapshots']['19.0']).items():
        shutil.copytree(m['path'], Path(a.out) / name)
    print('\n'.join(n for n in res['notes'] if 'regla' in n or 'script' in n))


def preview(a):
    """An Odoo 19 preview through the platform's path: the example ported, a slot prepared (base with demo data, external SQL,
    modules, admin password) and served by the slot's supervisor (container started by the workflow); then a real login over
    HTTP, and the slot released (its database dropped)."""
    import json as _json
    import urllib.request
    from mf.odoo import code as oc
    from mf.worker.runner import run
    src = Path(a.addons)
    ext = oj.external_sql(src, a.sql or [], None, print)
    port = oc.port_steps(src, ctx().workdir / 'ported', run, ctx(), print, ext=ext)
    import shutil
    flat = ctx().workdir / 'flat'   # one folder with the modules, as the platform keeps them (jobs.ported_addons)
    for name, m in oc.modules(port['snapshots']['19.0']).items():
        shutil.copytree(m['path'], flat / name)
    final = flat
    rows = [{'name': n, 'depends': list(m['manifest'].get('depends', []))} for n, m in oc.modules(final).items()]
    om = uuid.uuid4()
    res = oj.preview(ctx(), run, 'e2e-project', om, src, backup=None, addons19=str(final), rows=rows, sql_files=a.sql or [],
                     base_url='http://127.0.0.1', log=print, cancelled=lambda: False, timeout=3 * 3600)
    print('preview:', {k: v for k, v in res.items() if k != 'password'})
    url = f"http://127.0.0.1:{res['port']}"
    for _ in range(120):   # the supervisor starts Odoo
        try:
            if urllib.request.urlopen(url + '/web/login', timeout=10).status == 200:
                break
        except Exception:  # noqa: BLE001
            time.sleep(5)
    else:
        raise AssertionError('Odoo del espacio no respondió')
    body = _json.dumps({'jsonrpc': '2.0', 'params': {'db': res['database'], 'login': res['login'], 'password': res['password']}}).encode()
    ans = _json.loads(urllib.request.urlopen(urllib.request.Request(url + '/web/session/authenticate', data=body,
                                                                    headers={'Content-Type': 'application/json'}), timeout=60).read())
    assert (ans.get('result') or {}).get('uid'), ans
    print('login como', res['login'], 'uid', ans['result']['uid'], '· módulos', res['modules'])
    assert not oj.missing_modules(res['database'], res['modules']), oj.missing_modules(res['database'], res['modules'])
    chain.preview_release(res['slot'])
    for _ in range(40):
        if not chain.query('postgres')(f"select 1 from pg_database where datname = '{res['database']}'"):
            break
        time.sleep(3)
    else:
        raise AssertionError('el espacio no borró su base al apagarse')
    print('apagado: base borrada, espacio libre')


def main(argv):
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('scenario')
    s.add_argument('name')
    s.add_argument('dump')
    s.add_argument('--start', default='12.0')
    s.add_argument('--third-party', type=int, default=0)
    s.add_argument('--oca')
    s.add_argument('--table', action='append')
    s.add_argument('--code')
    s.add_argument('--same', action='append')
    k = sub.add_parser('code')
    k.add_argument('addons')
    k.add_argument('--sql', action='append')
    k.add_argument('--expect-sql', action='append')
    k.add_argument('--flows')
    v = sub.add_parser('preview')
    v.add_argument('addons')
    v.add_argument('--sql', action='append')
    q = sub.add_parser('port')
    q.add_argument('addons')
    q.add_argument('out')
    q.add_argument('--sql', action='append')
    a = p.parse_args(argv)
    if a.cmd == 'port':
        return port(a)
    if a.cmd == 'preview':
        chain.ensure_control()
        return preview(a)
    chain.ensure_control()
    scenario(a) if a.cmd == 'scenario' else code(a)


if __name__ == '__main__':
    main(sys.argv[1:])

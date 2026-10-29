"""What happens to each installed module of an Odoo 12 database on the way to 19, from public data only (rules/odoo, generated
by scripts/gen_odoo_index.py): the OpenUpgrade coverage of every hop for core modules, and the OCA branches for OCA modules.

Origin: core (in the OpenUpgrade table of the starting version: the manifest author is not proof, Odoo 12 gives "Odoo S.A." to
any module without one), OCA (author or the OCA index of the starting branch) or third party / custom.
A core module is followed through renames and merges hop by hop; a hop without a script for it is reported. An OCA module needs
its code in every intermediate version (OpenUpgrade migrates each module with the code of each release): the versions where it is
missing are listed. A third-party or custom module has no known ported code: it blocks until someone decides what to do."""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

VERSIONS = ('12.0', '13.0', '14.0', '15.0', '16.0', '17.0', '18.0', '19.0')
OCA_AUTHOR = 'Odoo Community Association'


@lru_cache
def data() -> tuple[dict, dict]:
    base = Path(os.environ.get('MF_RULES_CATALOG', Path(__file__).resolve().parents[3] / 'rules' / 'catalog.json')).parent / 'odoo'
    cov = json.loads((base / 'openupgrade-coverage.json').read_text(encoding='utf-8'))
    oca = json.loads((base / 'oca-modules.json').read_text(encoding='utf-8'))
    return cov, oca


def _core_path(name: str, hops: list) -> dict:
    """Follow a core module hop by hop: its name in each release, the coverage status of each hop, where it ends."""
    steps, current, gaps = [], name, []
    for h in hops:
        info = h['modules'].get(current)
        if info is None:  # not in that table: it disappeared silently or was never core of that branch
            steps.append({'hop': f"{h['from']}→{h['to']}", 'module': current, 'status': 'NO_INFO'})
            gaps.append(h['to'])
            continue
        st = info['status'] or ('NOTHING_TO_DO' if (info['extra'] or '').startswith('No DB layout changes') else 'NOT_COVERED')
        st = {'Done': 'DONE', 'Nothing to do': 'NOTHING_TO_DO'}.get(st, st)
        steps.append({'hop': f"{h['from']}→{h['to']}", 'module': current, 'status': st, 'note': info['extra']})
        if st == 'NOT_COVERED':
            gaps.append(h['to'])
        if info['flag'] == 'del':
            if not info['into']:
                import re
                adopted = re.search(r'Adopted in OCA as (\w+)', info['extra'] or '')
                return {'steps': steps, 'final': None, 'gaps': gaps, 'removedIn': h['to'], 'adopted': adopted[1] if adopted else None}
            current = info['into']
    return {'steps': steps, 'final': current, 'gaps': gaps, 'removedIn': None, 'adopted': None}


def classify(installed: list[dict], start: str = '12.0', custom=frozenset()) -> list[dict]:
    """installed: [{name, author, version}] from ir_module_module (state installed) of a database at start. One row per module
    with origin, availability per version, outcome (OK | WARN | BLOCK) and the reason in plain language. custom: modules whose code
    came with the project (ported version by version, mf.odoo.code): they migrate with their data instead of blocking."""
    cov, oca = data()
    hops = [h for h in cov['hops'] if float(h['from']) >= float(start)]
    core = {n for n, i in hops[0]['modules'].items() if i['flag'] != 'new'}
    branches = oca['branches']
    versions = [v for v in VERSIONS if float(v) >= float(start)]
    rows = []
    for m in sorted(installed, key=lambda x: x['name']):
        name, author = m['name'], (m.get('author') or '')
        row = {'name': name, 'author': author, 'version': m.get('version')}
        if name in core:
            p = _core_path(name, hops)
            row |= {'origin': 'CORE', 'path': p['steps'], 'final': p['final']}
            if p['removedIn'] and p['adopted']:
                row |= {'outcome': 'WARN', 'reason': f"Odoo lo retiró en {p['removedIn']} y OCA lo continúa como {p['adopted']}: para conservar sus datos "
                                                     f"hay que incluir {p['adopted']} (OCA) en la migración"}
            elif p['removedIn']:
                row |= {'outcome': 'WARN', 'reason': f"Odoo lo retiró en {p['removedIn']} y OpenUpgrade no indica a dónde pasa: revisar si su "
                                                     'funcionalidad quedó en otro módulo; sus datos propios no llegan a 19'}
            elif p['gaps']:
                row |= {'outcome': 'WARN', 'reason': 'OpenUpgrade no documenta script para los saltos a ' + ', '.join(p['gaps'])
                        + ': revisar sus datos después de migrar'}
            else:
                row |= {'outcome': 'OK', 'reason': 'Núcleo de Odoo con script u «sin cambios» en cada salto'
                        + (f" (en 19 se llama {p['final']})" if p['final'] != name else '')}
        elif OCA_AUTHOR in author or name in branches.get(start, {}):
            avail = {v: name in branches.get(v, {}) for v in versions}
            missing = [v for v in versions[1:] if not avail[v]]
            repo = next((branches[v][name] for v in reversed(versions) if name in branches.get(v, {})), None)
            row |= {'origin': 'OCA', 'repository': repo, 'availability': avail}
            if not missing:
                row |= {'outcome': 'OK', 'reason': f'OCA ({repo}): portado en todas las versiones hasta 19'}
            elif avail['19.0']:
                row |= {'outcome': 'BLOCK', 'reason': f"OCA ({repo}): existe en 19 pero no en {', '.join(missing)}: sin su código en esas "
                        'versiones sus datos no se migran'}
            else:
                row |= {'outcome': 'BLOCK', 'reason': f"OCA ({repo or 'repositorio desconocido'}): no está portado a 19"
                        + (f" (falta en {', '.join(missing)})" if missing else '')}
        elif name in custom:
            row |= {'origin': 'CUSTOM', 'outcome': 'WARN',
                    'reason': 'A la medida, con su código en el proyecto: se porta versión por versión y se migra con sus datos en cada '
                              'salto; revisar el inventario de la pantalla Código (cambios manuales y a revisar)'}
        else:
            row |= {'origin': 'THIRD_PARTY', 'outcome': 'BLOCK',
                    'reason': 'Módulo de terceros o a la medida: no hay versión 19 conocida; hay que aportar su código portado o '
                              'decidir desinstalarlo (sus datos no se migran)'}
        rows.append(row)
    return rows


def summary(rows: list[dict]) -> dict:
    by = lambda k, v: sum(1 for r in rows if r.get(k) == v)  # noqa: E731
    return {'modules': len(rows), 'core': by('origin', 'CORE'), 'oca': by('origin', 'OCA'), 'thirdParty': by('origin', 'THIRD_PARTY'),
            'custom': by('origin', 'CUSTOM'),
            'ok': by('outcome', 'OK'), 'warn': by('outcome', 'WARN'), 'block': by('outcome', 'BLOCK')}


if __name__ == '__main__':  # self-check against the generated data
    rows = {r['name']: r for r in classify([{'name': 'sale', 'author': 'Odoo S.A.'}, {'name': 'account', 'author': 'Odoo S.A.'},
                                            {'name': 'partner_firstname', 'author': 'Camptocamp, Odoo Community Association (OCA)'},
                                            {'name': 'x_acme_custom', 'author': 'ACME'},
                                            {'name': 'legacy_sales_custom', 'author': 'Odoo S.A.'}])}   # Odoo 12's default author
    assert rows['sale']['origin'] == 'CORE' and rows['sale']['final'] == 'sale', rows['sale']
    assert rows['partner_firstname']['origin'] == 'OCA' and rows['partner_firstname']['availability']['19.0']
    assert rows['x_acme_custom']['outcome'] == 'BLOCK' and rows['legacy_sales_custom']['origin'] == 'THIRD_PARTY'
    assert classify([{'name': 'legacy_sales_custom', 'author': ''}], custom={'legacy_sales_custom'})[0]['origin'] == 'CUSTOM'
    r16 = {r['name']: r for r in classify([{'name': 'sale', 'author': ''}, {'name': 'partner_firstname', 'author': ''}], start='16.0')}
    assert [s['hop'] for s in r16['sale']['path']] == ['16.0→17.0', '17.0→18.0', '18.0→19.0'], r16['sale']['path']
    assert list(r16['partner_firstname']['availability']) == ['16.0', '17.0', '18.0', '19.0']
    print({k: (v['outcome'], v['reason'][:70]) for k, v in rows.items()})

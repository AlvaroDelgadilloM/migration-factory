"""Deployment waves of a tomcat-war migration (docs/19, "Despliegue y rollback por ola").

The client config may group the modules in waves ({"waves": [{"name": "1", "title": "...", "modules": [...]}]}). One WAR
still carries every module; the deployment chooses the active waves (-Dmf.waves / context-param mf.waves) and the module
loader starts only theirs. A module of an active wave may call another module at runtime (direct-vm, an OSGi service it
imports): that link does not cross a WAR, so when the other module's wave is not active it is started anyway in INTERNAL
mode (its queue consumers and scheduled jobs stopped, its HTTP traffic still on the source system). That is what makes the
rollback of a wave independent: taking it out of mf.waves never breaks another active wave.

The links come from the migrated code: `from("direct-vm:x")` provides x, any other "direct-vm:x" uses it; ServiceExport
provides an interface, ServiceImport uses it."""
from __future__ import annotations

import re
from pathlib import Path


NAME = re.compile(r'[\w.\-]{1,40}')
_FROM_VM = re.compile(r'''(?:\bfrom\(\s*|<from\s+uri=)["']direct-vm:([\w.\-]+)''')
_ANY_VM = re.compile(r'''["']direct-vm:([\w.\-]+)''')
_EXPORT = re.compile(r'ServiceExport">.*?name="interfaceName" value="([^"]+)"', re.S)
_IMPORT = re.compile(r'ServiceImport">\s*<property name="interfaceName" value="([^"]+)"', re.S)



def InvalidInput(message: str) -> Exception:  # pipeline.InvalidInput, imported late: pipeline imports this module
    from .pipeline import InvalidInput as error
    return error(message)


def validate(waves) -> list:
    """Shape only (names, one wave per module); whether the modules exist is checked once the workspace is read."""
    if waves is None:
        return []
    if not isinstance(waves, list) or not all(isinstance(w, dict) for w in waves):
        raise InvalidInput('waves: lista de {"name", "title", "modules"}')
    seen, where = set(), {}
    for w in waves:
        name = str(w.get('name', ''))
        if not NAME.fullmatch(name):
            raise InvalidInput(f'waves: nombre de ola inválido «{name}» (letras, números, punto, guion; hasta 40)')
        if name in seen:
            raise InvalidInput(f'waves: la ola {name} está repetida')
        seen.add(name)
        mods = w.get('modules')
        if not isinstance(mods, list) or not mods or not all(isinstance(m, str) for m in mods):
            raise InvalidInput(f'waves: la ola {name} necesita la lista de módulos (artifactId)')
        for m in mods:
            if m in where:
                raise InvalidInput(f'waves: {m} está en las olas {where[m]} y {name}; cada módulo va en una sola')
            where[m] = name
    return waves


def links(ws: Path, modules: list) -> dict:
    """{module: {other module: [why, ...]}}: what each module calls in another one at runtime."""
    provides, exports, uses, imports = {}, {}, {}, {}
    for m in modules:
        name = m['artifactId']
        main = ws / 'modules' / name / 'src' / 'main'
        p, u, e, i = set(), set(), set(), set()
        for f in sorted(main.rglob('*')) if main.is_dir() else []:
            if f.suffix not in ('.java', '.xml'):
                continue
            text = f.read_text(encoding='utf-8', errors='replace')
            fr = set(_FROM_VM.findall(text))
            p |= fr
            u |= set(_ANY_VM.findall(text)) - fr
            e |= set(_EXPORT.findall(text))
            i |= set(_IMPORT.findall(text))
        provides[name], uses[name], exports[name], imports[name] = p, u - p, e, i - e
    vm_owner = {x: n for n, xs in provides.items() for x in xs}
    itf_owner = {x: n for n, xs in exports.items() for x in xs}
    out = {}
    for name in provides:
        need = {}
        for x in sorted(uses[name]):
            if vm_owner.get(x) not in (None, name):
                need.setdefault(vm_owner[x], []).append(f'direct-vm:{x}')
        for x in sorted(imports[name]):
            if itf_owner.get(x) not in (None, name):
                need.setdefault(itf_owner[x], []).append(f'servicio {x.rsplit(".", 1)[-1]}')
        out[name] = need
    return out


def plan(waves: list, modules: list, needs: dict) -> dict:
    """Per wave: its modules, the modules of OTHER waves it needs (started in internal mode when their wave is not active)
    and who needs it. Modules in no wave are always started (libraries, servlet registerers)."""
    names = {m['artifactId'] for m in modules}
    unknown = sorted({x for w in waves for x in w['modules']} - names)
    if unknown:
        raise InvalidInput('waves: módulos que no están en el workspace: ' + ', '.join(unknown))
    wave_of = {x: str(w['name']) for w in waves for x in w['modules']}
    rows = []
    for w in waves:
        name, own = str(w['name']), list(w['modules'])
        other = {}
        for m in own:
            for dep, why in needs.get(m, {}).items():
                if wave_of.get(dep, name) != name:  # same wave or always started: nothing to tell
                    other.setdefault(dep, {'wave': wave_of[dep], 'by': [], 'why': []})
                    other[dep]['by'].append(m)
                    other[dep]['why'] += [x for x in why if x not in other[dep]['why']]
        rows.append({'name': name, 'title': w.get('title') or '', 'modules': own,
                     'needs': [{'module': d} | v for d, v in sorted(other.items())]})
    for r in rows:
        r['neededBy'] = sorted({o['name'] for o in rows for n in o['needs'] if n['wave'] == r['name']})
        r['laterNeeds'] = [n for n in r['needs'] if _order(rows, n['wave']) > _order(rows, r['name'])]
    return {'waves': rows, 'unassigned': sorted(names - set(wave_of)), 'waveOf': wave_of}


def _order(rows: list, name: str) -> int:
    return next(i for i, r in enumerate(rows) if r['name'] == name)


def properties(module: str, plan_: dict | None, needs: dict) -> list[str]:
    """Lines for the module's module.properties."""
    if not plan_:
        return []
    out = []
    if module in plan_['waveOf']:
        out.append(f"wave={plan_['waveOf'][module]}")
    if needs.get(module):
        out.append('needs=' + ','.join(sorted(needs[module])))
    return out


def manual(plan_: dict) -> list[str]:
    """What the team must know before deploying wave by wave."""
    out = []
    for r in plan_['waves']:
        for n in r['laterNeeds']:
            out.append(f"Ola {r['name']}: usa {n['module']} de la ola {n['wave']}, que se libera después ({', '.join(n['why'][:3])}). "
                       f"Al activar la ola {r['name']}, {n['module']} arranca en modo interno (sin colas ni tareas); su tráfico HTTP sigue en el "
                       f"sistema actual hasta la ola {n['wave']}")
    return out


def markdown(plan_: dict) -> str:
    out = ['## Despliegue y rollback por ola', '',
           'Un mismo WAR trae todas las olas; cada despliegue elige las activas con `-Dmf.waves=<olas>` (o el context-param `mf.waves`). '
           'Revertir una ola = quitarla de `mf.waves`, reiniciar el contexto y devolver sus rutas al sistema actual. Un módulo de otra ola '
           'que una ola activa necesita arranca en **modo interno** (sin colas ni tareas programadas), así la reversión no rompe a las demás. '
           '`-Dmf.consumers.off=<olas>` mantiene detenidas las colas y tareas de esas olas durante el cambio.', '',
           '| Ola | Módulos | Necesita de otras olas (modo interno si no están activas) | La usan |', '|---|---|---|---|']
    for r in plan_['waves']:
        needs = '; '.join(f"{n['module']} (ola {n['wave']})" for n in r['needs']) or '—'
        out.append(f"| {r['name']}{' · ' + r['title'] if r['title'] else ''} | {', '.join(r['modules'])} | {needs} | "
                   f"{', '.join('ola ' + x for x in r['neededBy']) or '—'} |")
    if plan_['unassigned']:
        out += ['', f"Sin ola (arrancan siempre): {', '.join(plan_['unassigned'])}."]
    return '\n'.join(out) + '\n'

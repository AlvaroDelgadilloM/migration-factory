"""Regenerates rules/camel/camel2-to-camel3-types.json: Camel 2 classes that moved to another package in Camel 3, computed
from the real jars (never written by hand).

    python3 scripts/gen_camel2to3_map.py            (needs mvn and network to Maven Central)

Rule: a public top-level class of camel-core/camel-spring 2.24.3 that does not exist with the same name in Camel 3.22.4
(camel-core closure + camel-main, camel-spring, camel-jaxb) and whose simple name appears exactly once there, is a move.
Excluded (reported, never applied): package-info, targets outside the camel-core 3 closure (they need a new dependency and
may change semantics, e.g. org.apache.camel.spring.Main -> org.apache.camel.main.Main), a Spring class mapped out of Spring,
and simple names with several candidates. Classes with no Camel 3 counterpart are listed as removed (manual review)."""
import collections
import json
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path

CAMEL2, CAMEL3 = '2.24.3', '3.22.4'
OUT = Path(__file__).resolve().parents[1] / 'rules' / 'camel' / 'camel2-to-camel3-types.json'


def jars(artifacts, version, work: Path) -> list[Path]:
    work.mkdir(parents=True)
    deps = ''.join(f'<dependency><groupId>org.apache.camel</groupId><artifactId>{a}</artifactId><version>{version}</version></dependency>'
                   for a in artifacts)
    (work / 'pom.xml').write_text(f'<project><modelVersion>4.0.0</modelVersion><groupId>x</groupId><artifactId>x</artifactId>'
                                  f'<version>1</version><dependencies>{deps}</dependencies></project>')
    subprocess.run(['mvn', '-q', '-B', 'dependency:copy-dependencies', '-DincludeGroupIds=org.apache.camel', '-DoutputDirectory=jars'],
                   cwd=work, check=True)
    return sorted((work / 'jars').glob('*.jar'))


def classes(paths) -> dict:
    out = {}
    for j in paths:
        art = re.sub(r'-\d+\.\d+\.\d+\.jar$', '', j.name)
        for n in zipfile.ZipFile(j).namelist():
            if n.endswith('.class') and '$' not in n and not n.startswith('META-INF'):
                out[n[:-6].replace('/', '.')] = art
    return out


def main():
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        c2 = classes(jars(['camel-core', 'camel-spring'], CAMEL2, t / 'c2'))
        c3 = classes(jars(['camel-core', 'camel-main', 'camel-spring', 'camel-jaxb'], CAMEL3, t / 'c3'))
        core = {re.sub(r'-\d+\.\d+\.\d+\.jar$', '', j.name) for j in jars(['camel-core'], CAMEL3, t / 'core')}
    by_name = collections.defaultdict(list)
    for f in c3:
        by_name[f.rsplit('.', 1)[1]].append(f)
    moves, excluded, removed = {}, {}, []
    for old in sorted(set(c2) - set(c3)):
        if '.internal.' in old:
            continue
        name = old.rsplit('.', 1)[1]
        cands = by_name.get(name, [])
        if not cands:
            removed.append(old)
        elif name == 'package-info':
            continue
        elif len(cands) > 1:
            excluded[old] = f'varios candidatos en Camel 3: {", ".join(sorted(cands))}'
        elif c3[cands[0]] not in core:
            excluded[old] = f'{cands[0]} está en {c3[cands[0]]}, fuera de camel-core: requiere dependencia y revisión'
        elif ('.spring.' in old) != ('.spring.' in cands[0]):
            excluded[old] = f'{cands[0]}: clase de Spring mapeada fuera de Spring (otra semántica)'
        else:
            moves[old] = cands[0]
    OUT.write_text(json.dumps({
        'description': 'Clases de Camel 2 que en Camel 3 cambiaron de paquete; generado por scripts/gen_camel2to3_map.py desde los jars.',
        'from': f'camel-core + camel-spring {CAMEL2}', 'to': f'camel-core (cierre) + camel-main + camel-spring + camel-jaxb {CAMEL3}',
        'moves': moves, 'excluded': excluded, 'removed': removed}, ensure_ascii=False, indent=1) + '\n')
    print(f'{len(moves)} movidas, {len(excluded)} excluidas, {len(removed)} eliminadas -> {OUT}')


if __name__ == '__main__':
    main()

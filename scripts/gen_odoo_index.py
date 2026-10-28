"""Regenerates rules/odoo/: what the Odoo 12 -> 19 migration knows about modules, from public sources (never written by hand).

    python3 scripts/gen_odoo_index.py        (needs the GitHub CLI `gh`, authenticated; read-only API calls)

- openupgrade-coverage.json: per hop (12.0->13.0 ... 18.0->19.0) the OpenUpgrade coverage table of each core module (status,
  extra information, new/deleted, merged or renamed target), from docsource/modulesXX0-YY0.rst of the pinned OCA/OpenUpgrade
  commit of that branch.
- oca-modules.json: modules of every OCA repository that has the branch, for every branch 12.0 ... 19.0 (module -> repository),
  so a module of the database can be reported version by version as ported or not (its code is needed in every hop)."""
import json
import re
import subprocess
from datetime import date
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / 'rules' / 'odoo'
BRANCHES = ('12.0', '13.0', '14.0', '15.0', '16.0', '17.0', '18.0', '19.0')
HOPS = [('12.0', '13.0'), ('13.0', '14.0'), ('14.0', '15.0'), ('15.0', '16.0'), ('16.0', '17.0'), ('17.0', '18.0'), ('18.0', '19.0')]


def gh(*args) -> str:
    return subprocess.run(['gh', 'api', *args], check=True, capture_output=True, text=True).stdout


def coverage(src: str, dst: str) -> dict:
    sha = gh(f'repos/OCA/OpenUpgrade/commits/{dst}', '-q', '.sha').strip()
    name = f"modules{src.replace('.', '')}-{dst.replace('.', '')}.rst"
    import base64
    folder = 'odoo/openupgrade/doc/source' if dst == '13.0' else 'docsource'  # 13.0 is a full fork of Odoo
    text = base64.b64decode(gh(f'repos/OCA/OpenUpgrade/contents/{folder}/{name}?ref={sha}', '-q', '.content')).decode()
    mods = {}
    for line in text.splitlines():  # two table layouts: |module|status|extra| (14.0+) and |module|status and notes| (13.0)
        line = line.strip()
        if not line.startswith('|') or line.startswith('|=') or not line.endswith('|'):
            continue
        body = line[1:-1]
        flag = re.match(r'\s*\|(new|del)\|\s*', body)
        cells = [c.strip() for c in body[flag.end() if flag else 0:].split('|')]
        if len(cells) < 2 or not re.fullmatch(r'[\w.]+', cells[0]) or cells[0] == 'Module':
            continue
        raw = cells[1]
        extra = ' '.join(c for c in cells[2:] if c) or (raw if raw and not raw.rstrip('.') in ('Done', 'Nothing to do') else '')
        status = 'Done' if raw.startswith('Done') else 'Nothing to do' if raw.startswith('Nothing to do') else (raw or None)
        target = re.search(r'(?:Merged into|Renamed to|Moved to)\s+([\w]+)', extra)
        mods[cells[0]] = {'status': status, 'extra': extra or None, 'flag': flag[1] if flag else None, 'into': target[1] if target else None}
    return {'from': src, 'to': dst, 'commit': sha, 'source': f'OCA/OpenUpgrade@{sha[:12]}:{folder}/{name}', 'modules': mods}


def oca_modules(branch: str) -> dict:
    names = sorted(set(gh('orgs/OCA/repos', '--paginate', '-q', '.[] | select(.archived|not) | .name').split()))
    out = {}
    for repo in names:
        try:
            tree = json.loads(gh(f'repos/OCA/{repo}/git/trees/{branch}'))
        except subprocess.CalledProcessError:
            continue  # the repository has no such branch
        for e in tree.get('tree', []):
            if e['type'] == 'tree' and not e['path'].startswith(('.', 'setup')) and re.fullmatch(r'[a-z0-9_]+', e['path']):
                out.setdefault(e['path'], repo)
    return out


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'openupgrade-coverage.json').write_text(json.dumps(
        {'generated': date.today().isoformat(), 'hops': [coverage(s, d) for s, d in HOPS]}, ensure_ascii=False, indent=1) + '\n')
    oca = {b: oca_modules(b) for b in BRANCHES}
    (OUT / 'oca-modules.json').write_text(json.dumps({'generated': date.today().isoformat(), 'branches': oca}, ensure_ascii=False, indent=1) + '\n')
    print({b: len(v) for b, v in oca.items()})


if __name__ == '__main__':
    main()

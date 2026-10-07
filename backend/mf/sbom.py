"""SBOM and artifact hashes (improvements/11). The SBOM lists the *declared* dependencies of each module as Maven resolved
them (effective POM); transitive dependencies are not included (that needs a dependency-resolution plugin run)."""
import hashlib
import uuid
from pathlib import Path


def cyclonedx(effective: dict, tool_version: str, timestamp: str) -> dict:
    comps, seen = [], set()
    for (mg, ma), mod in sorted(effective.items(), key=lambda kv: str(kv[0])):
        for (g, a), v in sorted(mod['deps'].items(), key=lambda kv: str(kv[0])):
            if not g or not a or (g, a, v) in seen:
                continue
            seen.add((g, a, v))
            purl = f'pkg:maven/{g}/{a}' + (f'@{v}' if v else '')
            comps.append({'type': 'library', 'bom-ref': purl, 'group': g, 'name': a, 'version': v or '', 'purl': purl,
                          'properties': [{'name': 'mf:declaredBy', 'value': f'{mg}:{ma}'}]})
    return {'bomFormat': 'CycloneDX', 'specVersion': '1.5', 'serialNumber': f'urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, repr(sorted(seen)))}',
            'version': 1, 'metadata': {'timestamp': timestamp, 'tools': [{'vendor': 'migration-factory', 'name': 'migrate.py', 'version': tool_version}],
                                       'properties': [{'name': 'mf:scope', 'value': 'dependencias declaradas (efectivas), sin transitivas'}]},
            'components': comps}


def checksums(root: Path, dirs=('reports', 'migrated-project')) -> str:
    """sha256sum-compatible manifest of the delivered files (verify with `shasum -a 256 -c checksums.sha256` from the output dir)."""
    lines = []
    for d in dirs:
        for f in sorted((root / d).rglob('*')):
            if f.is_file() and f.name != 'checksums.sha256':
                lines.append(f'{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.relative_to(root).as_posix()}')
    return '\n'.join(lines) + '\n'


if __name__ == '__main__':  # self-check
    import tempfile
    eff = {('demo', 'app'): {'deps': {('org.apache.camel', 'camel-http'): '4.14.0', ('x', 'y'): None}}}
    b = cyclonedx(eff, '1', '2026-01-01T00:00:00Z')
    assert [c['purl'] for c in b['components']] == ['pkg:maven/org.apache.camel/camel-http@4.14.0', 'pkg:maven/x/y']
    assert b['serialNumber'] == cyclonedx(eff, '1', 'other')['serialNumber']  # same input, same SBOM identity
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / 'reports').mkdir()
        (Path(d) / 'reports' / 'a.md').write_text('x')
        assert checksums(Path(d)).endswith('  reports/a.md\n')
    print('ok')

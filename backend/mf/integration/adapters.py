"""Integration adapters (doc 19 §22-§23): generating the result is separate from integrating it.
ZIP download is always available; a pull request only when PR_CAPABILITY_GATE passes. Never inferred from a `.git` folder."""
from __future__ import annotations

PR_PROVIDERS = {'github'}  # providers with a working draft-PR adapter (mf/worker/providers.py)


def pr_capability(source_type: str, provider: str | None, pr_config: dict | None, blockers: list[str]) -> dict:
    """PR_CAPABILITY_GATE: remote Git source, supported provider, configured repository + credential reference, gates met."""
    reasons = []
    if source_type != 'GIT_REMOTE':
        return {'available': False, 'shown': False, 'reasons': ['Origen local: el resultado se entrega como ZIP descargable']}
    cfg = pr_config or {}
    if (cfg.get('provider') or provider) not in PR_PROVIDERS:
        reasons.append(f"Proveedor {cfg.get('provider') or provider or 'desconocido'} sin adaptador de PR")
    if not cfg.get('credentialRef'):
        reasons.append('Falta la referencia de credencial con permiso de escritura')
    if not cfg.get('repository') and not cfg.get('repo'):
        reasons.append('Falta el repositorio remoto destino del PR')
    reasons += [b for b in blockers if b not in reasons and 'solo están disponibles cuando el origen es un repositorio Git' not in b]
    return {'available': not reasons, 'shown': True, 'reasons': reasons}


def integrations(source_type: str, pr: dict) -> list[dict]:
    out = [{'id': 'zip-download', 'available': True}]
    if pr['shown']:
        out.append({'id': 'github-pr', 'available': pr['available'], 'reasons': pr['reasons']})
    return out


if __name__ == '__main__':  # self-check: acceptance cases 1-2 of part B
    local = pr_capability('LOCAL', 'local', None, [])
    assert local == {'available': False, 'shown': False, 'reasons': ['Origen local: el resultado se entrega como ZIP descargable']}
    ok = pr_capability('GIT_REMOTE', 'github', {'provider': 'github', 'repository': 'o/r', 'credentialRef': 'env:MF_CRED_GH'}, [])
    assert ok['available'] and ok['shown']
    no = pr_capability('GIT_REMOTE', 'gitlab', {'provider': 'gitlab', 'repository': 'o/r'}, ['Gate G4 obligatorio'])
    assert not no['available'] and len(no['reasons']) == 3
    assert [i['id'] for i in integrations('LOCAL', local)] == ['zip-download']
    print('ok')

"""Git provider adapters. Only GitHub draft PRs are implemented; the API host is fixed (no SSRF surface)."""
import httpx

GITHUB_API = 'https://api.github.com'


class ProviderError(Exception):
    pass


def create_draft_pr(cfg: dict, branch: str, *, title: str, body: str, token: str, transport=None) -> str:
    if cfg.get('provider') != 'github':
        raise ProviderError(f"Proveedor no soportado: {cfg.get('provider')}")
    with httpx.Client(base_url=GITHUB_API, timeout=30, transport=transport, follow_redirects=False) as c:
        r = c.post(f"/repos/{cfg['repository']}/pulls",
                   headers={'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json',
                            'X-GitHub-Api-Version': '2022-11-28'},
                   json={'title': title, 'head': branch, 'base': cfg.get('baseBranch', 'main'), 'body': body, 'draft': True})
    if r.status_code != 201:
        raise ProviderError(f'GitHub respondió {r.status_code}')
    url = r.json().get('html_url', '')
    if not url.startswith('https://github.com/'):
        raise ProviderError('Respuesta inesperada del proveedor')
    return url

"""Git provider adapters for draft pull/merge requests: GitHub, GitLab (gitlab.com or self-managed) and Bitbucket Cloud.
The API host is fixed per provider, or for self-managed GitLab the host of the project's repository URL, which already
passed the Git host allowlist and DNS checks (no SSRF surface beyond what the source itself uses)."""
from urllib.parse import quote, urlsplit

import httpx

GITHUB_API = 'https://api.github.com'
BITBUCKET_API = 'https://api.bitbucket.org/2.0'


class ProviderError(Exception):
    pass


def pr_config(cfg: dict) -> dict:
    """Project PR settings with camelCase keys; projects saved before the fix stored snake_case (credential_ref, base_branch)."""
    cfg = dict(cfg or {})
    for snake, camel in (('credential_ref', 'credentialRef'), ('base_branch', 'baseBranch')):
        if snake in cfg:
            cfg.setdefault(camel, cfg.pop(snake))
    return cfg


def create_draft_pr(cfg: dict, branch: str, *, title: str, body: str, token: str, repo_url: str = '', transport=None) -> str:
    provider, base = cfg.get('provider'), cfg.get('baseBranch', 'main')
    if provider == 'github':
        api, path, headers = GITHUB_API, f"/repos/{cfg['repository']}/pulls", {
            'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'}
        payload, web = {'title': title, 'head': branch, 'base': base, 'body': body, 'draft': True}, 'https://github.com/'
    elif provider == 'gitlab':
        host = urlsplit(repo_url).hostname or 'gitlab.com'
        api, path, headers = f'https://{host}/api/v4', f"/projects/{quote(cfg['repository'], safe='')}/merge_requests", {'PRIVATE-TOKEN': token}
        # GitLab marks a merge request as draft by its title prefix
        payload, web = {'source_branch': branch, 'target_branch': base, 'title': f'Draft: {title}', 'description': body}, f'https://{host}/'
    elif provider == 'bitbucket':
        api, path, headers = BITBUCKET_API, f"/repositories/{cfg['repository']}/pullrequests", {'Authorization': f'Bearer {token}'}
        payload = {'title': title, 'description': body, 'draft': True, 'close_source_branch': True,
                   'source': {'branch': {'name': branch}}, 'destination': {'branch': {'name': base}}}
        web = 'https://bitbucket.org/'
    else:
        raise ProviderError(f'Proveedor no soportado: {provider}')
    with httpx.Client(base_url=api, timeout=30, transport=transport, follow_redirects=False) as c:
        r = c.post(path, headers=headers, json=payload)
    if r.status_code != 201:
        raise ProviderError(f'{provider} respondió {r.status_code}')
    data = r.json()
    url = data.get('html_url') or data.get('web_url') or ((data.get('links') or {}).get('html') or {}).get('href') or ''
    if not url.startswith(web):
        raise ProviderError('Respuesta inesperada del proveedor')
    return url

"""End-to-end OIDC check against the local Keycloak profile (docker-compose.oidc.yml). Creates two test users with random
passwords (never stored), obtains real RS256 tokens and checks what the API accepts and rejects. Local use only.

    .venv/bin/python scripts/oidc_e2e.py   # reads KEYCLOAK_ADMIN_PASSWORD from .env
"""
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

KC = os.environ.get('MF_KEYCLOAK_URL', 'http://localhost:3242')
API = os.environ.get('MF_API_URL', 'http://127.0.0.1:3241/api/v1')


def env_file(key):
    for line in (Path(__file__).resolve().parents[1] / '.env').read_text().splitlines():
        if line.startswith(key + '='):
            return line.split('=', 1)[1]
    return os.environ.get(key)


def http(method, url, body=None, token=None, form=False):
    data = urllib.parse.urlencode(body).encode() if form else (json.dumps(body).encode() if body is not None else None)
    h = {'Content-Type': 'application/x-www-form-urlencoded' if form else 'application/json'}
    if token:
        h['Authorization'] = f'Bearer {token}'
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data, method=method, headers=h), timeout=20) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw and r.headers.get_content_type() == 'application/json' else raw)
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def token(user, password, client='mf-e2e'):
    st, t = http('POST', f'{KC}/realms/mf/protocol/openid-connect/token',
                 {'grant_type': 'password', 'client_id': client, 'username': user, 'password': password}, form=True)
    assert st == 200, (st, t)
    return t['access_token']


def main():
    for _ in range(100):  # Keycloak takes a while to start and import the realm
        try:
            if http('GET', f'{KC}/realms/mf/.well-known/openid-configuration')[0] == 200:
                break
        except (urllib.error.URLError, ConnectionError):
            pass
        time.sleep(3)
    else:
        sys.exit('Keycloak no respondió')
    st, t = http('POST', f'{KC}/realms/master/protocol/openid-connect/token',
                 {'grant_type': 'password', 'client_id': 'admin-cli', 'username': 'admin', 'password': env_file('KEYCLOAK_ADMIN_PASSWORD')}, form=True)
    assert st == 200, ('admin de Keycloak', st, t)
    admin = t['access_token']
    users = {}
    for name, is_admin in (('mf-e2e-admin', True), ('mf-e2e-dev', False)):
        pwd = secrets.token_urlsafe(18)
        http('POST', f'{KC}/admin/realms/mf/users', {'username': name, 'enabled': True, 'email': f'{name}@example.test', 'emailVerified': True,
                                                     'firstName': name, 'lastName': 'e2e',
                                                     'credentials': [{'type': 'password', 'value': pwd, 'temporary': False}]}, admin)
        st, found = http('GET', f'{KC}/admin/realms/mf/users?username={name}&exact=true', token=admin)
        uid = found[0]['id']
        http('PUT', f'{KC}/admin/realms/mf/users/{uid}/reset-password', {'type': 'password', 'value': pwd, 'temporary': False}, admin)
        if is_admin:
            st, role = http('GET', f'{KC}/admin/realms/mf/roles/mf-admin', token=admin)
            http('POST', f'{KC}/admin/realms/mf/users/{uid}/role-mappings/realm', [role], admin)
        users[name] = token(name, pwd)
    checks = []

    def check(label, ok):
        checks.append((label, ok))
        print(('PASS ' if ok else 'FAIL ') + label)

    st, cfg = http('GET', f'{API}/auth/config')
    check('auth/config anuncia oidc', st == 200 and cfg.get('mode') == 'oidc')
    check('sin token → 401', http('GET', f'{API}/projects')[0] == 401)
    check('token válido (RS256, JWKS, aud, iss) → 200', http('GET', f'{API}/projects', token=users['mf-e2e-dev'])[0] == 200)
    bad = users['mf-e2e-dev'][:-4] + ('AAAA' if not users['mf-e2e-dev'].endswith('AAAA') else 'BBBB')
    check('firma alterada → 401', http('GET', f'{API}/projects', token=bad)[0] == 401)
    check('token de otro realm/audiencia (admin de master) → 401', http('GET', f'{API}/projects', token=admin)[0] == 401)
    st, me = http('GET', f'{API}/me', token=users['mf-e2e-admin'])
    check('rol mf-admin del realm → admin en la plataforma', st == 200 and me.get('isAdmin') is True)
    st, me = http('GET', f'{API}/me', token=users['mf-e2e-dev'])
    check('usuario sin rol → no admin', st == 200 and me.get('isAdmin') is False)
    sys.exit(0 if all(ok for _, ok in checks) else 1)


if __name__ == '__main__':
    main()

"""Create or update the Portainer stack from GitHub Actions (stdlib only; secret values are never printed).

    python scripts/portainer_deploy.py <compose-file>

Environment:
  PORTAINER_URL          https://portainer.example.com:9443 (no trailing slash)
  PORTAINER_API_KEY      access token of a Portainer user (My account -> Access tokens)
  PORTAINER_ENDPOINT_ID  environment id (Home -> the environment: the number in the URL, usually 1 or 2)
  PORTAINER_STACK_NAME   default: migration-factory
  PORTAINER_INSECURE     "true" only for Portainer's self-signed certificate
  STACK_ENV              names of the variables passed to the stack, comma separated; values come from the environment
"""
import json
import os
import ssl
import sys
import urllib.error
import urllib.request


def main():
    url = os.environ['PORTAINER_URL'].rstrip('/')
    key = os.environ['PORTAINER_API_KEY']
    endpoint = os.environ['PORTAINER_ENDPOINT_ID']
    name = os.environ.get('PORTAINER_STACK_NAME') or 'migration-factory'
    ctx = ssl._create_unverified_context() if os.environ.get('PORTAINER_INSECURE') == 'true' else None
    compose = open(sys.argv[1], encoding='utf-8').read()
    env = [{'name': n, 'value': os.environ.get(n, '')} for n in (x.strip() for x in os.environ['STACK_ENV'].split(',')) if n]
    missing = [e['name'] for e in env if e['name'] in ('POSTGRES_PASSWORD', 'REDIS_PASSWORD', 'MF_DEV_JWT_SECRET') and not e['value']]
    if missing:
        sys.exit(f"Faltan secrets obligatorios: {', '.join(missing)}")

    def api(method, path, body=None):
        req = urllib.request.Request(url + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                     headers={'X-API-Key': key, 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=600, context=ctx) as r:
                return json.loads(r.read() or b'null')
        except urllib.error.HTTPError as e:
            sys.exit(f'Portainer {method} {path.split("?")[0]} -> {e.code}: {e.read()[:500].decode(errors="replace")}')

    stack = next((s for s in api('GET', '/api/stacks') if s['Name'] == name and str(s['EndpointId']) == str(endpoint)), None)
    if stack is None:
        api('POST', f'/api/stacks/create/standalone/string?endpointId={endpoint}', {'name': name, 'stackFileContent': compose, 'env': env})
        print(f'Stack {name} creado en el entorno {endpoint}')
    else:
        api('PUT', f"/api/stacks/{stack['Id']}?endpointId={endpoint}",
            {'stackFileContent': compose, 'env': env, 'prune': True, 'pullImage': True})
        print(f"Stack {name} (id {stack['Id']}) actualizado; imágenes descargadas de nuevo")


if __name__ == '__main__':
    main()

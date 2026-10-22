import os
import socket
from pathlib import Path

import os

import pytest

from mf.security import (UnsafeInput, find_secrets, redact, resolve_credential, safe_join, symlink_escapes,
                         validate_credential_ref, validate_git_url)


def fake_resolve(ip):
    return lambda host, port, proto=None: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, port))]


@pytest.mark.parametrize('url,code', [
    ('http://github.com/a/b.git', 'GIT_URL_SCHEME'),
    ('ssh://git@github.com/a/b.git', 'GIT_URL_SCHEME'),
    ('https://user:pw@github.com/a/b.git', 'GIT_URL_CREDENTIALS'),
    ('https://evil.example.com/a/b.git', 'GIT_HOST_NOT_ALLOWED'),
    ('https://github.com:8443/a/b.git', 'GIT_URL_PORT'),
    ('https://github.com/a/../b.git', 'GIT_URL_PATH'),
    ('https://github.com/a/b.git?x=1', 'GIT_URL_INVALID'),
    ('https://github.com/a/b.git\n', 'GIT_URL_INVALID'),
    ('file:///etc', 'GIT_URL_SCHEME'),
    ('file:///tmp/repo.git', 'GIT_URL_SCHEME'),
])
def test_git_url_rejected(url, code):
    with pytest.raises(UnsafeInput) as e:
        validate_git_url(url, resolve=fake_resolve('140.82.112.3'))
    assert e.value.code == code


@pytest.mark.parametrize('ip', ['127.0.0.1', '10.0.0.5', '169.254.169.254', '192.168.1.1', '::1', 'fd00::1'])
def test_allowed_host_resolving_to_private_ip_is_rejected(ip):
    with pytest.raises(UnsafeInput) as e:
        validate_git_url('https://github.com/a/b.git', resolve=lambda h, p, proto=None: [(0, 0, 0, '', (ip, p))])
    assert e.value.code == 'GIT_HOST_PRIVATE'


def test_valid_https(pilot_dir):
    assert validate_git_url('https://github.com/acme/orders.git', resolve=fake_resolve('140.82.112.3'))['provider'] == 'github'
    with pytest.raises(UnsafeInput):  # a local folder is never accepted as a Git URL
        validate_git_url('file://' + pilot_dir)


def test_credential_refs():
    assert validate_credential_ref('env:MF_CRED_GIT') == 'env:MF_CRED_GIT'
    for bad in ('ghp_secretvalue', 'env:PATH', 'env:MF_DATABASE_URL', 'vault:../x y'):
        with pytest.raises(UnsafeInput):
            validate_credential_ref(bad)
    assert resolve_credential('env:MF_CRED_X', {'MF_CRED_X': 's3cr3t'}) == 's3cr3t'
    with pytest.raises(UnsafeInput):
        resolve_credential('vault:kv/git', {})


def test_vault_kv2_references(monkeypatch):
    """vault:mount/path[#field] reads KV v2 with the worker's token; every failure is a clear, value-free error."""
    import httpx
    calls = []

    def fake_get(url, headers, timeout):
        calls.append((url, headers['X-Vault-Token']))
        if url.endswith('/missing'):
            return httpx.Response(404, json={'errors': []})
        if url.endswith('/denied'):
            return httpx.Response(403, json={'errors': ['permission denied']})
        return httpx.Response(200, json={'data': {'data': {'value': 'ghp_one', 'token': 'ghp_two'}}})
    monkeypatch.setattr(httpx, 'get', fake_get)
    env = {'MF_CRED_VAULT_TOKEN': 'hvs.worker'}
    with pytest.raises(UnsafeInput, match='MF_VAULT_ADDR'):
        resolve_credential('vault:kv/git', env)
    monkeypatch.setenv('MF_VAULT_ADDR', 'https://vault.internal:8200/')
    assert resolve_credential('vault:kv/team/git', env) == 'ghp_one'
    assert resolve_credential('vault:kv/team/git#token', env) == 'ghp_two'
    assert calls[0] == ('https://vault.internal:8200/v1/kv/data/team/git', 'hvs.worker')
    for ref, code in (('vault:kv/missing', 'VAULT_SECRET_NOT_FOUND'), ('vault:kv/denied', 'VAULT_FORBIDDEN'),
                      ('vault:kv/git#nope', 'VAULT_FIELD_NOT_FOUND'), ('vault:kv/../sys', 'CREDENTIAL_REF_INVALID')):
        with pytest.raises(UnsafeInput) as e:
            resolve_credential(ref, env)
        assert e.value.code == code
    with pytest.raises(UnsafeInput, match='MF_CRED_VAULT_TOKEN'):
        resolve_credential('vault:kv/git', {})


@pytest.mark.skipif(not os.environ.get('MF_VAULT_TEST_ADDR'), reason='real Vault: MF_VAULT_TEST_ADDR + MF_VAULT_TEST_TOKEN (dev server)')
def test_vault_real_server(monkeypatch):
    import httpx
    addr, token = os.environ['MF_VAULT_TEST_ADDR'], os.environ['MF_VAULT_TEST_TOKEN']
    httpx.post(f'{addr}/v1/secret/data/mf/git', headers={'X-Vault-Token': token}, json={'data': {'value': 'from-vault'}}).raise_for_status()
    monkeypatch.setenv('MF_VAULT_ADDR', addr)
    assert resolve_credential('vault:secret/mf/git', {'MF_CRED_VAULT_TOKEN': token}) == 'from-vault'
    with pytest.raises(UnsafeInput) as e:
        resolve_credential('vault:secret/mf/git', {'MF_CRED_VAULT_TOKEN': 'wrong-token'})
    assert e.value.code == 'VAULT_FORBIDDEN'


def test_redaction():
    text = ('password=hunter2 token: abc123 Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.abcdefghijklmnop '
            'https://bob:pw@host/x <password>p</password> ghp_' + 'a' * 36)
    out = redact(text, ['custom-secret-value'])
    for leaked in ('hunter2', 'abc123', 'bob:pw', '<password>p<', 'ghp_aaaa', 'eyJhbGci'):
        assert leaked not in out
    assert redact('value custom-secret-value here', ['custom-secret-value']) == 'value *** here'
    assert find_secrets('api_key=XYZ') and not find_secrets('import javax.jms.Queue;')


def test_safe_join_and_symlinks(tmp_path):
    assert safe_join(tmp_path, 'a/b.txt') == (tmp_path / 'a/b.txt').resolve()
    for bad in ('../x', '/etc/passwd', 'a/../../x', ''):
        with pytest.raises(UnsafeInput):
            safe_join(tmp_path, bad)
    (tmp_path / 'inside.txt').write_text('x')
    os.symlink(tmp_path / 'inside.txt', tmp_path / 'ok-link')
    os.symlink('/etc/hosts', tmp_path / 'escape-link')
    assert symlink_escapes(tmp_path) == ['escape-link']
    with pytest.raises(UnsafeInput):
        safe_join(tmp_path, 'escape-link')


def test_placeholders_are_not_secrets():
    assert redact('db.password=${MF_SECRET_DB_PASSWORD}') == 'db.password=${MF_SECRET_DB_PASSWORD}'
    assert not find_secrets('db.password=${MF_SECRET_DB_PASSWORD}\nurl={{services.x.url}}\napi_key=***')
    assert not find_secrets('from("sftp://u@h/in?password=RAW({{secrets.h.password}})&delay=1")')
    assert not find_secrets('.to("smtp://mail.local:25?from=legacy@legacy.local")')  # not userinfo
    assert find_secrets('ftp://bob:pw@host/x')
    assert find_secrets('db.password=Sup3rS3cret')


def test_maven_network_modes(tmp_path, monkeypatch):
    """improvements/06: offline adds -o; central ignores extra repositories; allowlist keeps the approved ones."""
    import time
    from mf.config import get_settings
    from mf.worker import mavenops, runner
    seen = {}
    monkeypatch.setattr(runner, 'run', lambda args, cwd, ctx, **kw: seen.setdefault('args', args))
    ctx = runner.Context('t', tmp_path, deadline=time.monotonic() + 60)
    (tmp_path / 'pom.xml').write_text('<project/>')
    monkeypatch.setenv('MF_MAVEN_EXTRA_REPOS', 'shib|https://build.shibboleth.net/maven/releases')
    for mode, offline, extra in (('offline', True, False), ('central', False, False), ('allowlist', False, True)):
        monkeypatch.setenv('MF_MAVEN_NETWORK', mode)
        get_settings.cache_clear()
        seen.clear()
        mavenops.mvn(ctx, tmp_path, ['validate'], 'x')
        assert ('-o' in seen['args']) is offline, mode
        assert ('shibboleth' in (tmp_path / 'meta' / 'settings.xml').read_text()) is extra, mode
    monkeypatch.setenv('MF_MAVEN_NETWORK', 'internet')
    get_settings.cache_clear()
    with pytest.raises(Exception):
        get_settings()
    monkeypatch.delenv('MF_MAVEN_NETWORK')
    monkeypatch.delenv('MF_MAVEN_EXTRA_REPOS')
    get_settings.cache_clear()


def test_maven_execution_guard(tmp_path, monkeypatch):
    """Doc 20 §14: Maven runs with -f <ProjectContext pom>; without pom.xml it is never started (MISSING_CANDIDATE_POM)."""
    import time
    from mf import preflight
    from mf.worker import mavenops, runner
    started = []
    monkeypatch.setattr(runner, 'run', lambda args, cwd, ctx, **kw: started.append(args))
    ctx = runner.Context('t', tmp_path, deadline=time.monotonic() + 60)
    res = mavenops.mvn(ctx, tmp_path / 'candidate', ['compile'], 'candidate-compile')
    assert res.exit_code == 1 and not started
    errs = preflight.classify(res.log_path.read_text())
    assert errs[0]['category'] == 'CANDIDATE_BUILD_CONFIGURATION_ERROR' and 'MISSING_CANDIDATE_DIRECTORY' in errs[0]['message']
    (tmp_path / 'candidate').mkdir()
    (tmp_path / 'candidate' / 'pom.xml').write_text('<project/>')
    mavenops.mvn(ctx, tmp_path / 'candidate', ['compile'], 'candidate-compile')
    assert started[0][started[0].index('-f') + 1] == str(tmp_path / 'candidate' / 'pom.xml')
    assert preflight.classify('[ERROR] The goal you specified requires a project to execute but there is no POM in this directory')[0]['category'] \
        == 'CANDIDATE_BUILD_CONFIGURATION_ERROR'

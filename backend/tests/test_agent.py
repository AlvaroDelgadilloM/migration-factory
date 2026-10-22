"""Local agent: the code stays on the user's machine; the platform receives facts, applies its own catalog, plans, and refuses
to execute (the migration runs where the code is)."""
import copy
import json

import httpx

from mf import cli_tool

API = '/api/v1'


def agent_project(client, token, name='Agente'):
    r = client.post(f'{API}/projects', headers=token('ana.arquitecta'), json={'name': name, 'sourceType': 'agent', 'targetRuntime': 'spring'})
    assert r.status_code == 201, r.text
    return r.json()['id']


def test_agent_publishes_facts_and_the_platform_applies_its_catalog(client, token, pilot_dir, monkeypatch, tmp_path):
    pid = agent_project(client, token, f'Agente {tmp_path.name[-6:]}')
    r = client.post(f'{API}/projects/{pid}/scans', headers=token('ana.arquitecta'), json={})
    assert r.status_code == 409 and r.json()['code'] == 'AGENT_SOURCE'                 # nothing to scan on the platform
    report = cli_tool.analyze(cli_tool.Path(pilot_dir), 'spring', None, False, lambda m: None)
    payload = cli_tool.agent_payload(report, 'spring')
    assert all(set(f) == {'ruleId', 'file', 'line', 'evidence'} for f in payload['report']['findings'])   # facts only

    tampered = copy.deepcopy(payload)
    tampered['report']['findings'][0] |= {'severity': 'low', 'blocking': False, 'recommendation': 'ignorar'}
    url = f'{API}/projects/{pid}/agent-scans'
    hdr = token('ana.arquitecta') | {'Content-Type': 'application/json'}
    bad = copy.deepcopy(payload)
    bad['report']['findings'].append({'ruleId': 'NOT_A_RULE', 'file': 'x', 'line': 1, 'evidence': ''})
    assert client.post(url, content=json.dumps(bad), headers=hdr).json()['code'] == 'UNKNOWN_RULES'
    assert client.post(url, content=json.dumps(payload | {'snapshotHash': 'x'}), headers=hdr).status_code == 422
    assert client.post(url, content=json.dumps(payload), headers=token('aldo.auditor') | {'Content-Type': 'application/json'}).status_code == 404

    calls = []
    monkeypatch.setenv('MF_AGENT_TOKEN', hdr['Authorization'].split()[1])
    monkeypatch.setattr(httpx, 'post', lambda u, content, headers, timeout: calls.append(u) or client.post(u.split('http://localhost')[1], content=content, headers=headers))
    assert cli_tool.main(['agent', 'push', pilot_dir, '--platform', 'http://localhost', '--project-id', pid, '--no-maven']) == 0
    assert calls == [f'http://localhost{url}']
    scans = client.get(f'{API}/projects/{pid}/scans', headers=token('ana.arquitecta')).json()['items']
    scan = scans[0]
    assert scan['status'] == 'succeeded' and scan['snapshotHash'] == report['snapshotHash']
    first = tampered['report']['findings'][0]
    r = client.post(url, content=json.dumps(tampered), headers=hdr)
    assert r.status_code == 201
    stored = [f for f in client.get(f"{API}/scans/{r.json()['scanId']}/findings?limit=200", headers=hdr).json()['items']
              if f['ruleId'] == first['ruleId'] and f['file'] == first['file'] and f['line'] == first['line']][0]
    assert stored['recommendation'] != 'ignorar'                                       # the catalog decides, not the agent

    prof = next(p for p in client.get(f'{API}/profiles', headers=hdr).json() if p['runtime'] == 'spring')
    plan = client.post(f"{API}/scans/{r.json()['scanId']}/plans", headers=hdr, json={'profileId': prof['id']})
    assert plan.status_code == 201, plan.text                                         # inventory, findings and plan work
    ex = client.post(f"{API}/plans/{plan.json()['id']}/preview", headers=hdr)
    assert ex.status_code == 409 and ex.json()['code'] == 'AGENT_SOURCE'


def test_agent_needs_a_token_and_https(monkeypatch, pilot_dir):
    monkeypatch.delenv('MF_AGENT_TOKEN', raising=False)
    assert cli_tool.main(['agent', 'push', pilot_dir, '--platform', 'https://mf.example', '--project-id', 'x', '--no-maven']) != 0
    monkeypatch.setenv('MF_AGENT_TOKEN', 't')
    assert cli_tool.main(['agent', 'push', pilot_dir, '--platform', 'http://mf.example', '--project-id', 'x', '--no-maven']) != 0

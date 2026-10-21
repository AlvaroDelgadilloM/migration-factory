"""Evidence report of the tomcat-war profile: what `verify routes` and `verify responses` found, as pages a person can
read and as screenshots that can be attached to a ticket or a gate.

Nothing is tested here: the report shows the results already saved in <out>/reports. Each case gets its own page with
the answer of the original system next to the answer of the migrated one, and a browser in headless mode photographs it.
Without a browser the pages are still written and the result says the screenshots are missing.
"""
from __future__ import annotations

import difflib
import html
import json
import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from xml.dom import minidom

from . import verify

BROWSERS = ('msedge', 'chrome', 'google-chrome', 'chromium', 'chromium-browser')
WINDOWS_BROWSERS = (r'Microsoft\Edge\Application\msedge.exe', r'Google\Chrome\Application\chrome.exe')
STATE = {'SAME': ('Idéntico', 'ok'), 'DIFFERENT': ('Distinto', 'bad'), 'ACCEPTED': ('Diferencia aceptada', 'warn'), 'NOT_EXECUTABLE': ('Sin ejecutar', 'bad'),
         'PASS': ('Aprobado', 'ok'), 'PASS_WITH_ACCEPTED': ('Aprobado con diferencias aceptadas', 'warn'), 'FAIL': ('Con diferencias', 'bad'),
         'REVIEW': ('Por revisar', 'warn')}
KIND = {'ERROR_TEXT': 'Mismo código de error; cambia solo el texto del error', 'DATA': 'Cambia el código, el tipo o los datos de la respuesta'}
MAX_LINES = 400  # per side, in a page

CSS = """
body{font:14px/1.45 "Segoe UI",Arial,sans-serif;color:#1c2430;background:#fff;margin:0;padding:24px 32px}
h1{font-size:22px;margin:0 0 4px} h2{font-size:17px;margin:28px 0 8px} p{margin:6px 0}
.muted{color:#5b6675} code,pre{font:12px/1.4 Consolas,"Courier New",monospace}
.tag{display:inline-block;padding:2px 10px;border-radius:10px;font-weight:600;font-size:12px}
.ok{background:#dff3e4;color:#17602d} .bad{background:#fbe1e1;color:#8f1d1d} .warn{background:#fff0cc;color:#7a5200}
.totals{display:flex;gap:12px;margin:14px 0} .totals div{border:1px solid #d8dee6;border-radius:6px;padding:10px 16px;min-width:110px}
.totals b{display:block;font-size:24px}
table{border-collapse:collapse;width:100%} th,td{border:1px solid #d8dee6;padding:6px 9px;text-align:left;vertical-align:top}
th{background:#f1f4f8} a{color:#0b5cad;text-decoration:none}
.sides{display:flex;gap:14px;margin-top:12px} .side{flex:1;min-width:0;border:1px solid #d8dee6;border-radius:6px}
.side h3{margin:0;padding:8px 12px;background:#f1f4f8;font-size:14px;border-bottom:1px solid #d8dee6}
.side .meta{padding:8px 12px;border-bottom:1px solid #d8dee6} .side pre{margin:0;padding:8px 0;white-space:pre-wrap;word-break:break-all}
.side pre span{display:block;padding:0 12px;min-height:1.4em} .side pre span.d{background:#ffe9a8}
.req{background:#f7f9fb;border:1px solid #d8dee6;border-radius:6px;padding:8px 12px;margin-top:10px}
.shot{max-width:100%;border:1px solid #d8dee6;margin:8px 0}
"""


def find_browser(explicit: str | None = None) -> str | None:
    explicit = explicit or os.environ.get('MF_BROWSER')  # the worker image sets it (Playwright's Chromium)
    if explicit:
        return explicit if Path(explicit).is_file() else shutil.which(explicit)
    for name in BROWSERS:
        found = shutil.which(name)
        if found:
            return found
    for base in filter(None, (os.environ.get(v) for v in ('ProgramFiles(x86)', 'ProgramFiles', 'LocalAppData'))):
        for rel in WINDOWS_BROWSERS:
            if (Path(base) / rel).is_file():
                return str(Path(base) / rel)
    return None


def shoot(cmd: list, png: Path, timeout: int = 60):
    """One headless screenshot. The browser runs in its own process group and the whole group is stopped afterwards: a
    headless Edge/Chrome sometimes never exits (or leaves children behind) and would hold the run, or pile up, forever."""
    kw = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)
    t0, size, exited = time.monotonic(), -1, None
    try:
        while time.monotonic() - t0 < timeout:
            now = png.stat().st_size if png.exists() else 0
            if now and (p.poll() is not None or now == size):  # written: the browser exited or the file stopped growing
                return
            size = now
            if p.poll() is not None:
                exited = exited or time.monotonic()
                if time.monotonic() - exited > 20:  # exited without writing it
                    return
            time.sleep(0.5)
    finally:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/PID', str(p.pid), '/T', '/F'], capture_output=True)
        else:
            import signal
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass


def pretty(body: str, ctype: str) -> list:
    """The body as lines, formatted when it is JSON or XML so both sides line up. The comparison itself was done on the raw text."""
    text = body or ''
    try:
        if 'json' in ctype or text.lstrip()[:1] in ('{', '['):
            text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
        elif text.lstrip().startswith('<') and 'html' not in ctype:
            text = '\n'.join(x for x in minidom.parseString(text.encode('utf-8')).toprettyxml(indent='  ').splitlines() if x.strip())
    except Exception:  # not well formed: show it as it came
        pass
    lines = text.splitlines() or ['(sin cuerpo)']
    return lines[:MAX_LINES] + ([f'... ({len(lines) - MAX_LINES} líneas más)'] if len(lines) > MAX_LINES else [])


def _marked(a: list, b: list) -> tuple[str, str]:
    da, db = set(), set()
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op != 'equal':
            da.update(range(i1, i2))
            db.update(range(j1, j2))
    return (''.join(f'<span class="{"d" if i in d else ""}">{html.escape(x)}</span>' for i, x in enumerate(lines)) for lines, d in ((a, da), (b, db)))


def _tag(state: str) -> str:
    label, css = STATE.get(state, (state, 'warn'))
    return f'<span class="tag {css}">{label}</span>'


def _page(title: str, body: str) -> str:
    return f'<!doctype html><html lang="es"><head><meta charset="utf-8"><title>{html.escape(title)}</title><style>{CSS}</style></head><body>{body}</body></html>'


def case_page(n: int, c: dict, labels: tuple, urls: tuple) -> tuple[str, int]:
    """(html, number of text lines of the longest side) for one case."""
    a, b = pretty(c['original'].get('body', ''), c['original'].get('type', '')), pretty(c['migrated'].get('body', ''), c['migrated'].get('type', ''))
    ma, mb = _marked(a, b)
    req = c.get('request') or {}
    sent = ''.join(f'<br><code>{html.escape(k)}: {html.escape(str(v))}</code>' for k, v in (req.get('headers') or {}).items())
    if req.get('body'):
        sent += f'<pre>{html.escape(req["body"][:3000])}</pre>'
    note = ''
    if c.get('issues'):
        note = f'<p><b>Diferencia:</b> {html.escape("; ".join(c["issues"]))}' + (f' — {KIND[c["kind"]]}' if c.get('kind') in KIND else '') + '</p>'
    if c.get('accept'):
        note += f'<p><b>Aceptada:</b> {html.escape(c["accept"])}</p>'
    sides = ''
    for label, url, r, marked in ((labels[0], urls[0], c['original'], ma), (labels[1], urls[1], c['migrated'], mb)):
        sides += (f'<div class="side"><h3>{html.escape(label)}</h3><div class="meta"><span class="muted">{html.escape(url)}</span><br>'
                  f'HTTP <b>{r["status"]}</b> · Content-Type <code>{html.escape(r.get("type") or "(ninguno)")}</code></div><pre>{marked}</pre></div>')
    body = (f'<h1>Caso {n}: {html.escape(c["name"])} {_tag(c["state"])}</h1>'
            f'<div class="req"><b>Petición enviada a los dos sistemas:</b> <code>{html.escape(c["method"])} {html.escape(c["path"])}</code>{sent}</div>'
            f'{note}<div class="sides">{sides}</div><p class="muted">Las líneas resaltadas son las que no coinciden.</p>')
    return _page(f'Caso {n}: {c["name"]}', body), max(len(a), len(b)) + sent.count('<br>') + (req.get('body') or '').count('\n')


def _routes_section(r: dict | None) -> str:
    if not r:
        return '<h2>Estructura de las rutas</h2><p class="muted">No se ha ejecutado <code>verify routes</code>.</p>'
    t = r.get('totals', {})
    names = {'identical': 'idénticas', 'unexpected': 'distintas sin explicar', 'expectedByPatch': 'distintas por parche documentado',
             'notDumped': 'sin comparar', 'notExecutable': 'sin ejecutar'}
    cells = ''.join(f'<div><b>{v}</b>{html.escape(names.get(k, str(k)))}</div>' for k, v in t.items())
    return (f'<h2>Estructura de las rutas {_tag(r["status"])}</h2><p>Se arma cada ruta con el código original y con el migrado y se compara '
            f'paso por paso, sin levantar nada.</p><div class="totals">{cells}</div><p class="muted">Detalle en <code>reports/route-structure.md</code>.</p>')


def report(out: Path, browser: str | None = None, screenshots: bool = True, labels: tuple = ('ORIGINAL', 'MIGRADO'), log=lambda m: None) -> dict:
    out = Path(out).resolve()
    reports = out / 'reports'
    if not (reports / 'responses.json').exists():
        raise verify.ToolError(f'No hay resultados que mostrar: falta {reports / "responses.json"} (ejecute antes verify responses)')
    res = json.loads((reports / 'responses.json').read_text(encoding='utf-8'))
    if any('body' not in c['original'] for c in res['cases']):
        raise verify.ToolError('responses.json se generó con una versión anterior y no guarda las respuestas completas: ejecute de nuevo verify responses')
    routes = json.loads((reports / 'route-structure.json').read_text(encoding='utf-8')) if (reports / 'route-structure.json').exists() else None
    evidence = json.loads((reports / 'equivalence-evidence.json').read_text(encoding='utf-8')) if (reports / 'equivalence-evidence.json').exists() else {}
    target = reports / 'evidence'
    shutil.rmtree(target, ignore_errors=True)
    (target / 'screenshots').mkdir(parents=True)
    urls = (res['original'], res['migrated'])

    pages, rows = [], ''
    for n, c in enumerate(res['cases'], 1):
        page, lines = case_page(n, c, labels, urls)
        name = f'caso-{n:03d}'
        (target / f'{name}.html').write_text(page, encoding='utf-8')
        pages.append((name, lines))
        kind = KIND.get(c.get('kind'), '')
        rows += (f'<tr><td>{n}</td><td><a href="{name}.html">{html.escape(c["name"])}</a></td><td><code>{html.escape(c["method"])} {html.escape(c["path"])}</code></td>'
                 f'<td>{c["original"]["status"]}</td><td>{c["migrated"]["status"]}</td><td>{_tag(c["state"])}</td>'
                 f'<td>{html.escape("; ".join(c.get("issues") or []))}{"<br><span class=muted>" + kind + "</span>" if kind else ""}'
                 f'{"<br>Aceptada: " + html.escape(c["accept"]) if c.get("accept") else ""}</td><td><a href="screenshots/{name}.png">captura</a></td></tr>')
    t = res['totals']
    kinds = res.get('differencesByKind') or {}
    summary = (f'<h1>Evidencia de equivalencia: original contra migrado {_tag(evidence.get("status", res["status"]))}</h1>'
               f'<p class="muted">Generado el {datetime.now():%Y-%m-%d %H:%M} · {html.escape(labels[0])}: {html.escape(urls[0])} · {html.escape(labels[1])}: {html.escape(urls[1])}</p>'
               f'<h2>Respuestas {_tag(res["status"])}</h2><p>La misma petición se envía a los dos sistemas y se compara el código HTTP, el Content-Type y el cuerpo.</p>'
               f'<div class="totals"><div><b>{t["cases"]}</b>casos</div><div><b>{t["same"]}</b>idénticos</div><div><b>{t["different"]}</b>distintos</div>'
               f'<div><b>{t["accepted"]}</b>aceptados</div><div><b>{t["notExecutable"]}</b>sin ejecutar</div></div>'
               + (f'<p>De los distintos: {kinds.get("ERROR_TEXT", 0)} devuelven el mismo código de error y cambia solo el texto; '
                  f'{kinds.get("DATA", 0)} cambian el código, el tipo o los datos.</p>' if t['different'] else '')
               + '<table><tr><th>#</th><th>Caso</th><th>Petición</th><th>HTTP original</th><th>HTTP migrado</th><th>Resultado</th><th>Diferencia</th><th>Captura</th></tr>'
               + rows + '</table>' + _routes_section(routes)
               + '<h2>Alcance</h2><p class="muted">El sistema original de esta prueba es el código original ejecutado con su versión de Camel en un servidor local. '
                 'No sustituye la prueba contra los sistemas reales del cliente.</p>')
    (target / 'index.html').write_text(_page('Evidencia de equivalencia', summary), encoding='utf-8')
    pages.insert(0, ('index', len(res['cases']) * 3 + 45))

    shots, problem = 0, None
    exe = find_browser(browser) if screenshots else None
    if screenshots and not exe:
        problem = 'No se encontró Edge ni Chrome; indique el ejecutable con --browser'
    if exe:
        # its own profile: a headless run must not touch the user's browser session. One per page: with a shared profile the
        # second launch hands the work to the first process and returns before the image exists.
        profiles = out / '.verify' / 'browser-profiles'
        for name, lines in pages:
            png = target / 'screenshots' / f'{name}.png'
            height = min(max(700, 330 + lines * 19), 12000)
            cmd = [exe, '--headless=new', '--disable-gpu', '--hide-scrollbars', '--no-first-run', f'--user-data-dir={profiles / name}',
                   f'--window-size=1500,{height}', f'--screenshot={png}', (target / f'{name}.html').as_uri()]
            if os.environ.get('MF_BROWSER_CONTAINER') == '1':
                # in the worker container (non-root, no user namespaces, small /dev/shm) Chromium's sandbox cannot start; it
                # only opens the local evidence pages written just above
                cmd[1:1] = ['--no-sandbox', '--disable-dev-shm-usage']
            try:
                shoot(cmd, png)
            except OSError as e:
                problem = f'{name}: {e}'
            if png.exists() and png.stat().st_size:
                shots += 1
                log(f'captura {png.name}')
            else:
                problem = problem or f'El navegador no generó {png.name}'
        shutil.rmtree(profiles, ignore_errors=True)
    result = {'report': str(target / 'index.html'), 'pages': len(pages), 'screenshots': shots, 'screenshotsDir': str(target / 'screenshots'),
              'browser': exe, 'problem': problem, 'status': evidence.get('status', res['status']), 'totals': t}
    (target / 'evidence.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result

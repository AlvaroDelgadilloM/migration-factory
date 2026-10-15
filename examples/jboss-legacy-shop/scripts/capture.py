"""Llama a los servicios de la tienda y guarda lo que devuelven (código HTTP y cuerpo) como evidencia, para compararlos
después contra la versión migrada.   python3 scripts/capture.py <base-url> <carpeta-salida>
   ej. legado:   python3 scripts/capture.py http://127.0.0.1:18081/shop/api evidence/legacy"""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

CASES = [
    ('cliente-existente', 'GET', '/customers/42', None),
    ('cliente-inexistente', 'GET', '/customers/999999', None),
    ('pedidos-de-cliente', 'GET', '/customers/42/orders', None),
    ('productos-por-categoria', 'GET', '/products?category=bienestar', None),
    ('productos-todos', 'GET', '/products', None),
    ('top-productos', 'GET', '/stats/top-products', None),
    ('pedido-valido', 'POST', '/orders', {'customerId': 42, 'productId': 7, 'quantity': 2}),
    ('pedido-cantidad-invalida', 'POST', '/orders', {'customerId': 42, 'productId': 7, 'quantity': 500}),
    ('pedido-sin-producto', 'POST', '/orders', {'customerId': 42, 'quantity': 1}),
]


def call(base, method, path, body):
    req = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={'Content-Type': 'application/json', 'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.headers.get('Content-Type', ''), r.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get('Content-Type', ''), e.read().decode('utf-8', 'replace')


def main():
    base, out = sys.argv[1].rstrip('/'), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    summary = []
    for name, method, path, body in CASES:
        status, ctype, text = call(base, method, path, body)
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = text
        (out / f'{name}.json').write_text(json.dumps({'request': {'method': method, 'path': path, 'body': body},
                                                      'status': status, 'contentType': ctype, 'response': parsed},
                                                     ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        size = len(parsed) if isinstance(parsed, list) else 1
        summary.append(f'{status}  {method:4} {path:34} -> {size} elemento(s)  {str(parsed)[:90]}')
    (out / 'resumen.txt').write_text('\n'.join(summary) + '\n', encoding='utf-8')
    (out / 'evidencia.html').write_text(html(base, out), encoding='utf-8')
    print('\n'.join(summary))


def html(base, out):
    """Readable evidence page: one card per service with status and the (first items of the) JSON response."""
    import html as h
    cards = []
    for name, method, path, body in CASES:
        d = json.loads((out / f'{name}.json').read_text(encoding='utf-8'))
        resp = d['response']
        shown = resp[:3] + [f'... ({len(resp) - 3} más)'] if isinstance(resp, list) and len(resp) > 3 else resp
        color = '#1a7f37' if d['status'] < 300 else '#9a6700' if d['status'] < 500 else '#cf222e'
        req = f' {h.escape(json.dumps(body, ensure_ascii=False))}' if body else ''
        cards.append(f'<section><h2><span style="background:{color}">{d["status"]}</span> {method} {h.escape(path)}{req}</h2>'
                     f'<pre>{h.escape(json.dumps(shown, ensure_ascii=False, indent=2))}</pre></section>')
    return ('<!doctype html><meta charset="utf-8"><title>Evidencia de servicios</title><style>'
            'body{font:14px system-ui;margin:24px;background:#f6f8fa;color:#1f2328}h1{font-size:20px}'
            'section{background:#fff;border:1px solid #d0d7de;border-radius:8px;padding:12px 16px;margin:12px 0}'
            'h2{font-size:15px;margin:0 0 8px}h2 span{color:#fff;border-radius:6px;padding:2px 8px;margin-right:6px}'
            'pre{margin:0;font-size:12.5px;max-height:240px;overflow:auto}</style>'
            f'<h1>Evidencia de servicios · {h.escape(base)}</h1>' + ''.join(cards))


if __name__ == '__main__':
    main()

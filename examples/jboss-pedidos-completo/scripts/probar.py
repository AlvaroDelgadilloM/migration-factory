"""Prueba de punta a punta del sistema de pedidos: REST, SOAP publicado y consumido, base, transacción, colas, tarea programada y
archivos. Guarda cada respuesta como evidencia (para comparar después contra la versión migrada) y falla si alguna no es la
esperada.
   python3 scripts/probar.py <base-url> <carpeta-evidencia> [--entrada <carpeta montada en /opt/pedidos/entrada>]
   ej. python3 scripts/probar.py http://127.0.0.1:18083/pedidos evidence/legacy --entrada entrada"""
import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

SOAP = ('<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/" xmlns:p="http://demo.example/pedidos">'
        '<soap:Body><p:consultarPedido><p:pedidoId>{}</p:pedidoId></p:consultarPedido></soap:Body></soap:Envelope>')


def call(base, method, path, body=None, soap=False):
    data, headers = None, {'Accept': 'application/json'}
    if soap:
        data, headers = body.encode(), {'Content-Type': 'text/xml; charset=utf-8', 'SOAPAction': '"consultarPedido"'}
    elif body is not None:
        data = (body if isinstance(body, str) else json.dumps(body)).encode()
        headers['Content-Type'] = 'application/json' if not isinstance(body, str) else 'text/plain'
    req = urllib.request.Request(base + path, method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.headers.get('Content-Type', ''), r.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get('Content-Type', ''), e.read().decode('utf-8', 'replace')


def parsed(text):
    try:
        return json.loads(text)
    except ValueError:
        return text


def until(fn, seconds=40):
    end = time.time() + seconds
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(2)
    return fn()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('base')
    ap.add_argument('out')
    ap.add_argument('--entrada', help='carpeta montada en /opt/pedidos/entrada (prueba de archivos)')
    a = ap.parse_args()
    base, out = a.base.rstrip('/'), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    api = base + '/api'
    results = []

    def check(name, method, path, body=None, status=200, expect=None, soap=False, url=None):
        st, ctype, text = call(url or api, method, path, body, soap)
        value = parsed(text)
        ok = bool(st == status and (expect is None or expect(value, text)))
        results.append({'caso': name, 'metodo': method, 'ruta': path, 'esperado': status, 'status': st, 'ok': ok})
        (out / f'{name}.json').write_text(json.dumps({'status': st, 'contentType': ctype.split(';')[0], 'body': value}, ensure_ascii=False, indent=2),
                                          encoding='utf-8')
        print(('OK   ' if ok else 'FALLA'), name, st, (text[:160] if not ok else ''), flush=True)
        return value

    def stock(producto):
        prods = parsed(call(api, 'GET', '/productos')[2])
        return next(p['existencias'] for p in prods if p['id'] == producto)

    check('salud', 'GET', '/salud', expect=lambda v, t: v.get('estado') == 'OK')
    check('cliente-existente', 'GET', '/clientes/1', expect=lambda v, t: v['nombre'] == 'Comercial Andina')
    check('cliente-inexistente', 'GET', '/clientes/999', status=404, expect=lambda v, t: v['error'] == 'NO_ENCONTRADO')
    check('productos-por-categoria', 'GET', '/productos?categoria=utensilios', expect=lambda v, t: [p['id'] for p in v] == [3, 4])
    check('pedido-cantidad-invalida', 'POST', '/pedidos', {'clienteId': 1, 'productoId': 1, 'cantidad': 0}, 400,
          lambda v, t: v['error'] == 'VALIDACION')
    check('pedido-sin-producto', 'POST', '/pedidos', {'clienteId': 1, 'cantidad': 1}, 400, lambda v, t: 'productoId' in v['detalle'])
    check('pedido-cliente-inactivo', 'POST', '/pedidos', {'clienteId': 3, 'productoId': 1, 'cantidad': 1}, 422,
          lambda v, t: v['error'] == 'CLIENTE_INACTIVO')
    check('pedido-sin-existencias', 'POST', '/pedidos', {'clienteId': 1, 'productoId': 3, 'cantidad': 1}, 409,
          lambda v, t: v['error'] == 'SIN_EXISTENCIAS')
    check('pedido-credito-rechazado', 'POST', '/pedidos', {'clienteId': 4, 'productoId': 1, 'cantidad': 1}, 422,
          lambda v, t: v['error'] == 'CREDITO_RECHAZADO')
    check('pedido-credito-sin-respuesta', 'POST', '/pedidos', {'clienteId': 5, 'productoId': 1, 'cantidad': 1}, 503,
          lambda v, t: v['error'] == 'CREDITO_NO_DISPONIBLE')
    antes = stock(5)
    check('pedido-rollback', 'POST', '/pedidos', {'clienteId': 1, 'productoId': 5, 'cantidad': 20}, 500, lambda v, t: v['error'] == 'ERROR_INTERNO')
    results.append({'caso': 'rollback-existencias-intactas', 'ok': stock(5) == antes, 'antes': antes, 'despues': stock(5)})

    nuevo = check('pedido-valido', 'POST', '/pedidos', {'clienteId': 1, 'productoId': 1, 'cantidad': 2}, 201,
                  lambda v, t: v['estado'] == 'PENDIENTE' and v['folio'] == 'CR-1-379' and float(v['total']) == 379.0)
    pid = nuevo['pedidoId'] if isinstance(nuevo, dict) else 0
    confirmado = until(lambda: parsed(call(api, 'GET', f'/pedidos/{pid}')[2]).get('estado') == 'CONFIRMADO')
    results.append({'caso': 'pedido-confirmado-por-la-cola', 'ok': bool(confirmado)})
    check('pedido-consulta', 'GET', f'/pedidos/{pid}', expect=lambda v, t: v['estado'] == 'CONFIRMADO')
    check('pedido-cancelar', 'POST', f'/pedidos/{pid}/cancelar', '', 200, lambda v, t: v['estado'] == 'CANCELADO')
    check('pedido-cancelar-otra-vez', 'POST', f'/pedidos/{pid}/cancelar', '', 409, lambda v, t: v['error'] == 'NO_CANCELABLE')
    vencido = until(lambda: parsed(call(api, 'GET', '/pedidos/1')[2]).get('estado') == 'VENCIDO')
    results.append({'caso': 'tarea-programada-vence-pedido-1', 'ok': bool(vencido)})

    check('soap-consultar-pedido', 'POST', '/pedidos', SOAP.format(2), 200, soap=True, url=base + '/ws',
          expect=lambda v, t: re.search(r'<(\w+:)?estado>CONFIRMADO<', t) and re.search(r'<(\w+:)?folio>CR-1-840<', t))
    check('soap-pedido-inexistente', 'POST', '/pedidos', SOAP.format(999), 500, soap=True, url=base + '/ws',
          expect=lambda v, t: 'Fault' in t and 'Pedido no encontrado' in t)
    check('soap-wsdl', 'GET', '/pedidos?wsdl', url=base + '/ws', expect=lambda v, t: 'consultarPedido' in t)
    check('reporte-inventario-xslt', 'GET', '/reportes/inventario',
          expect=lambda v, t: '<reporteInventario productos="5"' in t and 'agotado="true"' in t)

    check('mensaje-invalido-a-la-cola', 'POST', '/pedidos/confirmaciones', 'no-es-un-numero', 202)
    dlq = until(lambda: any(e['detalle'] == 'no-es-un-numero' for e in parsed(call(api, 'GET', '/eventos?tipo=DLQ')[2])))
    results.append({'caso': 'mensaje-invalido-en-rechazados', 'ok': bool(dlq)})
    error_credito = until(lambda: len(parsed(call(api, 'GET', '/eventos?tipo=ERROR_CREDITO')[2])) > 0)
    results.append({'caso': 'error-de-credito-avisado-por-cola', 'ok': bool(error_credito)})

    if a.entrada:
        antes = stock(2)
        Path(a.entrada, 'reabasto.csv').write_text('producto,cantidad\n2,15\n4,5\n', encoding='utf-8')
        repuesto = until(lambda: stock(2) == antes + 15)
        results.append({'caso': 'archivo-csv-reabastece', 'ok': bool(repuesto), 'antes': antes, 'despues': stock(2)})

    (out / 'resumen.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    bad = [r for r in results if not r['ok']]
    print(f"{len(results) - len(bad)} de {len(results)} casos correctos", flush=True)
    for r in bad:
        print('  falla:', r, flush=True)
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()

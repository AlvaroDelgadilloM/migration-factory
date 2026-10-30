"""Business flows of an Odoo project (pantalla Pruebas): Python checks written by the team against the ported modules, run with
`odoo shell` in an isolated Odoo 19 with demo data (odoo-hop-19 runner, no internet, no access to the platform). The script is the
helpers below + the team's checks + done(); each check is recorded (ok, what it returned or the error) and none stops the others.
The repository's own flows (backend/tests/e2e/odoo_flows) use the same helpers."""
from __future__ import annotations

import json
from pathlib import Path

HELPERS = Path(__file__).with_name('flow_helpers.py')   # read as text and sent to odoo shell; never imported by the platform

TEMPLATE = '''# Flujos de negocio de este proyecto en Odoo 19 (con datos demo). Cada check(nombre, función) se ejecuta en su propio savepoint:
# si la función lanza un error o un assert falla, ese flujo queda en FALLA y los demás siguen. Lo que devuelve se muestra.
# Ayudas disponibles: env (el entorno de Odoo), MODULES (sus módulos), product(), order(cliente, cantidad, precio, **campos),
# lot(), y las excepciones de Odoo (from odoo.exceptions import UserError, ValidationError).

def modulos_instalados():
    instalados = env['ir.module.module'].search([('name', 'in', MODULES), ('state', '=', 'installed')]).mapped('name')
    assert sorted(instalados) == sorted(MODULES), set(MODULES) - set(instalados)
    return f'{len(instalados)} módulo(s)'
check('los módulos portados están instalados', modulos_instalados)


def pedido_confirmado():
    cliente = env['res.partner'].create({'name': 'Cliente de prueba'})
    pedido = order(cliente, 2, 100)          # crea y confirma un pedido de venta
    assert pedido.state == 'sale', pedido.state
    return pedido.name
check('se puede confirmar un pedido de venta', pedido_confirmado)

# Ejemplo de un flujo propio (quite el # y ajuste los nombres a sus módulos):
# def credito():
#     cliente = env['res.partner'].create({'name': 'MF', 'x_credit_limit': 50000})
#     order(cliente, 10, 1200)
#     cliente.invalidate_recordset()
#     assert cliente.x_credit_risk == 12000, cliente.x_credit_risk
#     return cliente.x_credit_risk
# check('riesgo de crédito = suma de pedidos confirmados', credito)
'''


def script(content: str, modules: list[str]) -> str:
    """The script `odoo shell` reads: the helpers, the project's modules, the team's checks, then the results line."""
    return (HELPERS.read_text(encoding='utf-8') + f'\nMODULES = {sorted(modules)!r}\n\n' + content + '\n\ndone()\n')


def results(log: str) -> list[dict] | None:
    """The checks from the shell's output (the MF_FLOW line), or None when the script did not get to the end."""
    for line in reversed(log.splitlines()):
        if line.startswith('MF_FLOW '):
            try:
                return json.loads(line[len('MF_FLOW '):])
            except ValueError:
                return None
    return None


if __name__ == '__main__':  # self-check
    s = script("check('x', lambda: 1)", ['b', 'a'])
    assert "MODULES = ['a', 'b']" in s and s.rstrip().endswith('done()') and 'def check(' in s
    assert results('ruido\nMF_FLOW [{"check": "x", "ok": true, "got": "1"}]\n') == [{'check': 'x', 'ok': True, 'got': '1'}]
    assert results('Traceback ...') is None
    compile(script(TEMPLATE, ['a']), 'flows', 'exec')
    print('ok')

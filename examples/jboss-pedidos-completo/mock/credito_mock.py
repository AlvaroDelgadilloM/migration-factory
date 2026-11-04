"""Buró de crédito simulado (SOAP 1.1) que consume la aplicación. Según el cliente: 4 = rechazo (SOAP Fault), 5 = no responde a
tiempo (tarda más que el timeout del cliente), el resto = aprobado con un folio CR-<cliente>-<monto entero>."""
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

NS = 'http://proveedor.example/credito'
ENV = ('<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body>{}</soap:Body></soap:Envelope>')


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get('Content-Length') or 0)).decode('utf-8', 'replace')
        cliente = (re.search(r'<(?:\w+:)?clienteId>\s*(\d+)', body) or [None, '0'])[1]
        monto = (re.search(r'<(?:\w+:)?monto>\s*([\d.]+)', body) or [None, '0'])[1]
        if cliente == '5':
            time.sleep(8)
        if cliente == '4':
            status, payload = 500, ('<soap:Fault><faultcode>soap:Client</faultcode>'
                                    '<faultstring>Credito rechazado por el buro</faultstring></soap:Fault>')
        else:
            status, payload = 200, (f'<c:evaluarResponse xmlns:c="{NS}"><c:aprobado>true</c:aprobado>'
                                    f'<c:folio>CR-{cliente}-{int(float(monto))}</c:folio></c:evaluarResponse>')
        out = ENV.format(payload).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'text/xml; charset=utf-8')
        self.send_header('Content-Length', str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, fmt, *args):
        print('buro', self.address_string(), fmt % args, flush=True)


ThreadingHTTPServer(('0.0.0.0', 8090), Handler).serve_forever()

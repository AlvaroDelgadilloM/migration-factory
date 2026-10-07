"""Uniform error envelope: {code, message, requestId, details}."""
import logging
import uuid

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

log = logging.getLogger('mf.api')


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details=None):
        self.status, self.code, self.message, self.details = status, code, message, details or {}


def _body(request: Request, code, message, details=None):
    return {'code': code, 'message': message, 'requestId': getattr(request.state, 'request_id', None), 'details': details or {}}


def install(app):
    @app.middleware('http')
    async def request_id(request: Request, call_next):
        rid = request.headers.get('x-request-id', '')
        request.state.request_id = rid if (rid and len(rid) <= 64 and rid.replace('-', '').isalnum()) else uuid.uuid4().hex
        response = await call_next(request)
        response.headers['X-Request-Id'] = request.state.request_id
        return response

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError):
        return JSONResponse(_body(request, exc.code, exc.message, exc.details), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        details = [{'loc': e['loc'], 'msg': e['msg']} for e in exc.errors()]
        return JSONResponse(_body(request, 'VALIDATION_ERROR', 'Parámetros inválidos', {'errors': details}), status_code=422)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        log.exception('unhandled error request_id=%s', getattr(request.state, 'request_id', None))
        return JSONResponse(_body(request, 'INTERNAL', 'Error interno'), status_code=500)

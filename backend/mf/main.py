"""FastAPI application. Long work is queued; nothing here runs Maven, Git or recipes."""
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import errors
from .api import agent, apex, catalog, odoo as odoo_api, datastage, effort, executions, http_tests, metrics, modernization, projects, results, scans, uploads, workspace_migrations
from .config import get_settings

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title='Migration Factory API', version='1.0.0', openapi_url='/api/v1/openapi.json', docs_url='/api/v1/docs',
                  redoc_url=None)
    errors.install(app)
    app.add_middleware(CORSMiddleware, allow_origins=s.list_of(s.cors_origins), allow_credentials=False,
                       allow_methods=['GET', 'POST', 'PATCH', 'PUT', 'DELETE'],
                       allow_headers=['Authorization', 'Content-Type', 'If-Match', 'Idempotency-Key', 'Last-Event-ID', 'X-Request-Id', 'X-Filename', 'X-Migration-Type'],
                       expose_headers=['ETag', 'X-Request-Id'])
    for r in (projects.router, scans.router, executions.router, catalog.router, uploads.router, effort.router, metrics.router, modernization.router, results.router,
              workspace_migrations.router, http_tests.router, datastage.router, agent.router, odoo_api.router, apex.router):
        app.include_router(r, prefix='/api/v1')

    @app.get('/healthz', include_in_schema=False)
    def health():
        return {'status': 'ok'}

    @app.middleware('http')
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('Referrer-Policy', 'no-referrer')
        return response
    return app


app = create_app()

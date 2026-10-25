"""
FastAPI/uvicorn transport for CRRO (documented entry point):

    CRRO_DEMO_MODE=1 uvicorn api:app --port 8000

All behaviour lives in crro.api.http.HttpApp; this adapter only converts
FastAPI requests/responses, so it carries no business logic of its own.
NOTE: this adapter was not executed in the offline environment used to build
this version (FastAPI unavailable there); CI runs tests/integration/test_fastapi_adapter.py.
"""
import os

from fastapi import FastAPI, Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from crro.api.http import HttpApp, Request as CrroRequest
from crro.runtime import RecoveryRuntime, Settings

app = FastAPI(title="CRRO API", version="1.0.0", docs_url=None, redoc_url=None, openapi_url=None)
_http: HttpApp | None = None


def get_http() -> HttpApp:
    global _http
    if _http is None:
        dash = os.environ.get("CRRO_DASHBOARD_DIR")
        _http = HttpApp(RecoveryRuntime(Settings.from_env()), dash if dash and os.path.isdir(dash) else None)
    return _http


@app.on_event("startup")
def _startup():
    get_http()


@app.api_route("/{path:path}", methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"])
async def dispatch(path: str, request: Request):
    body = await request.body()   # exact raw bytes: required for webhook signature verification
    req = CrroRequest(request.method, "/" + path, dict(request.query_params), dict(request.headers), body,
                      request.client.host if request.client else "unknown")
    resp = await run_in_threadpool(get_http().handle, req)
    return Response(content=resp.bytes(), status_code=resp.status, headers=resp.headers,
                    media_type=resp.content_type)

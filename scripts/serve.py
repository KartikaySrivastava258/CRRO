#!/usr/bin/env python3
"""Standard-library HTTP transport for crro.api.http.HttpApp.

Used where FastAPI/uvicorn are unavailable (this repository's offline test
environment) and for the E2E/latency harness. Same handlers as api.py.
    CRRO_DEMO_MODE=1 python3 scripts/serve.py --port 8000 [--dashboard crro-dashboard/dist]
"""
import argparse
import logging
import os
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlsplit

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crro.api.http import HttpApp, Request  # noqa: E402
from crro.runtime import RecoveryRuntime, Settings  # noqa: E402


def make_handler(app: HttpApp):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        timeout = 30

        def _go(self):
            parts = urlsplit(self.path)
            n = int(self.headers.get("Content-Length") or 0)
            if n > 1024 * 1024:
                self.send_error(413)
                return
            body = self.rfile.read(n) if n else b""
            resp = app.handle(Request(self.command, parts.path, dict(parse_qsl(parts.query)),
                                      dict(self.headers.items()), body, self.client_address[0]))
            data = resp.bytes()
            self.send_response(resp.status)
            self.send_header("Content-Type", resp.content_type)
            self.send_header("Content-Length", str(len(data)))
            for k, v in resp.headers.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = do_PATCH = do_OPTIONS = do_PUT = do_DELETE = _go

        def log_message(self, *a):  # structured logs come from HttpApp
            pass
    return H


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--dashboard", default=None)
    a = ap.parse_args()
    logging.basicConfig(level=os.environ.get("CRRO_LOG_LEVEL", "INFO"), format="%(message)s")
    app = HttpApp(RecoveryRuntime(Settings.from_env()), a.dashboard)
    srv = ThreadingHTTPServer((a.host, a.port), make_handler(app))
    srv.daemon_threads = True
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=srv.shutdown).start())
    print(f"CRRO listening on http://{a.host}:{a.port} ({app._env_label()})", flush=True)
    try:
        srv.serve_forever()
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
API latency under a fixed workload, measured from a client over real sockets.
Server: scripts/serve.py transport (stdlib ThreadingHTTPServer) + SQLite file DB,
same handlers as the FastAPI adapter -- these are NOT uvicorn/PostgreSQL numbers.
Each scenario: warm-up W requests, then N measured requests at concurrency C.
"""
import concurrent.futures as cf, json, os, subprocess, sys, tempfile, time, urllib.request, uuid
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from scripts.benchmarks._env import environment  # noqa: E402

PORT, TOKEN = 8765, "demo-operator-token-local-only"
BASE = f"http://127.0.0.1:{PORT}"
N, C, W = int(os.environ.get("N", 600)), int(os.environ.get("C", 8)), 50
d = tempfile.mkdtemp()
env = dict(os.environ, CRRO_DEMO_MODE="1", CRRO_RATE_LIMIT_SCALE="100000", CRRO_DATABASE_URL=f"sqlite:///{d}/lat.db", CRRO_LOG_LEVEL="ERROR")
srv = subprocess.Popen([sys.executable, "scripts/serve.py", "--port", str(PORT)], env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(60):
    try:
        urllib.request.urlopen(BASE + "/health/live", timeout=1)
        break
    except Exception:
        time.sleep(0.5)


def call(method, path, body=None, headers=None):
    h = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                 method=method, headers=h)
    t = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            r.read()
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    return (time.perf_counter() - t) * 1000, code


def pf(_):
    k = uuid.uuid4().hex[:12]
    return call("POST", "/v1/events/payment-failed", {"external_payment_id": "lat" + k, "customer_ref": "lc" + k,
                "amount_inr": "2500.00", "payment_method": "card", "failure_reason": "card_expired"},
                {"Idempotency-Key": "lat" + k})


def run(name, fn):
    with cf.ThreadPoolExecutor(C) as ex:
        list(ex.map(fn, range(W)))
        t = time.perf_counter()
        res = list(ex.map(fn, range(N)))
        wall = time.perf_counter() - t
    lat = sorted(x for x, _ in res)
    codes = {}
    for _, c in res:
        codes[c] = codes.get(c, 0) + 1
    q = lambda p: round(lat[min(len(lat) - 1, int(p / 100 * len(lat)))], 2)
    r = {"scenario": name, "n": N, "concurrency": C, "warmup": W, "p50_ms": q(50), "p95_ms": q(95), "p99_ms": q(99),
         "max_ms": round(lat[-1], 2), "throughput_rps": round(N / wall, 1), "status_codes": codes,
         "valid": set(codes) <= {200, 201}}
    print(json.dumps(r), flush=True)
    return r


try:
    case_ids = []
    results = [run("GET /v1/overview", lambda _: call("GET", "/v1/overview")),
               run("POST /v1/events/payment-failed (decide + dispatch, unique keys)", pf),
               run("POST /v1/events/payment-failed (idempotent replay, same key)",
                   lambda _: call("POST", "/v1/events/payment-failed", {"external_payment_id": "replay1",
                                  "customer_ref": "r1", "amount_inr": "2500.00", "payment_method": "card",
                                  "failure_reason": "card_expired"}, {"Idempotency-Key": "replay-key"})),
               run("GET /v1/cases?limit=25", lambda _: call("GET", "/v1/cases?limit=25"))]
    json.dump({"environment": environment(), "server": "scripts/serve.py (stdlib ThreadingHTTPServer), SQLite WAL file",
               "client": f"Python urllib, ThreadPoolExecutor({C}), same host", "label": "local, single host, not production",
               "rate_limits": "relaxed via CRRO_RATE_LIMIT_SCALE=100000 so the workload, not the limiter, is measured",
               "results": results}, open("docs/benchmarks/results/api_latency.json", "w"), indent=1)
finally:
    srv.terminate()

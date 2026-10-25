// Thin fetch client: bearer auth, request ids, timeouts, normalised errors.
const BASE = typeof __CRRO_API_BASE__ !== "undefined" ? __CRRO_API_BASE__ : "";
const KEY = "crro.token";

export const auth = {
  get: () => sessionStorage.getItem(KEY),
  set: (t) => sessionStorage.setItem(KEY, t),
  clear: () => sessionStorage.removeItem(KEY),
};

export class ApiError extends Error {
  constructor(status, body, requestId) {
    super(body?.error?.message || (status === 0 ? "Network unreachable" : `HTTP ${status}`));
    this.status = status; this.code = body?.error?.code; this.details = body?.error?.details;
    this.requestId = requestId;
  }
}

const rid = () => "ui_" + Math.random().toString(36).slice(2, 12) + Date.now().toString(36);

export async function request(method, path, { body, headers = {}, timeoutMs = 30000, signal, raw = false } = {}) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(new DOMException("timeout", "TimeoutError")), timeoutMs);
  if (signal) signal.addEventListener("abort", () => ctrl.abort(signal.reason));
  const token = auth.get();
  let res;
  try {
    res = await fetch(BASE + path, {
      method, signal: ctrl.signal,
      headers: { "X-Request-ID": rid(), ...(token ? { Authorization: `Bearer ${token}` } : {}),
                 ...(body !== undefined ? { "Content-Type": "application/json" } : {}), ...headers },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch (e) {
    clearTimeout(timer);
    if (e?.name === "AbortError" && signal?.aborted) throw e;
    throw new ApiError(0, { error: { message: e?.name === "TimeoutError" || ctrl.signal.reason?.name === "TimeoutError"
      ? "Request timed out" : "Network unreachable — is the API running?" } });
  }
  clearTimeout(timer);
  const requestId = res.headers.get("X-Request-ID");
  if (raw && res.ok) return res;
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (res.status === 401) { auth.clear(); window.dispatchEvent(new Event("crro:unauthorized")); }
  if (!res.ok) throw new ApiError(res.status, data, requestId);
  return data;
}

export const api = {
  ready: () => request("GET", "/health/ready"),
  me: () => request("GET", "/v1/me"),
  overview: () => request("GET", "/v1/overview"),
  cases: (p = {}) => request("GET", "/v1/cases?" + new URLSearchParams(clean(p))),
  case: (id) => request("GET", `/v1/cases/${encodeURIComponent(id)}`),
  approvals: (p = {}) => request("GET", "/v1/approvals?" + new URLSearchParams(clean(p))),
  decide: (id, decision, note) => request("POST", `/v1/approvals/${encodeURIComponent(id)}/decision`, { body: { decision, note } }),
  resolve: (attemptId) => request("POST", `/v1/attempts/${encodeURIComponent(attemptId)}/resolve`, { body: {} }),
  audit: (p = {}) => request("GET", "/v1/audit?" + new URLSearchParams(clean(p))),
  auditExport: (p = {}) => request("GET", "/v1/audit/export?" + new URLSearchParams(clean(p)), { raw: true }),
  unmatched: () => request("GET", "/v1/provider-events/unmatched"),
  runs: () => request("GET", "/v1/optimizer/runs?limit=10"),
  run: (id) => request("GET", `/v1/optimizer/runs/${encodeURIComponent(id)}`),
  startRun: (cfg) => request("POST", "/v1/optimizer/runs", { body: cfg, timeoutMs: 120000 }),
  runCsv: (id) => request("GET", `/v1/optimizer/runs/${encodeURIComponent(id)}/policies.csv`, { raw: true }),
  model: () => request("GET", "/v1/model"),
  benchmark: () => request("GET", "/v1/benchmarks/latest"),
  simulate: (ref, body) => request("POST", `/v1/simulator/payment-links/${encodeURIComponent(ref)}/events`, { body }),
};

function clean(p) { return Object.fromEntries(Object.entries(p).filter(([, v]) => v !== undefined && v !== null && v !== "")); }

export async function download(resPromise, filename) {
  const res = await resPromise;
  const blob = await res.blob();
  const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: filename });
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

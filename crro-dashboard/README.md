# CRRO dashboard

Operator console for the CRRO API. React 19 only — no UI framework, router, chart or HTTP
libraries (fetch, a hash router and hand-built accessible SVG/CSS charts), bundled with esbuild.

```bash
npm install
npm run build                      # -> dist/ (served by the API: scripts/serve.py --dashboard dist)
CRRO_API_BASE=http://127.0.0.1:8000 npm run build   # if serving the UI from another origin (add it to CRRO_ALLOWED_ORIGINS)
../scripts/run_e2e.sh --screenshots ../docs/screenshots   # browser E2E (Playwright + Chromium)
```

Pages: Overview, Cases, Case detail, Approvals, Optimizer, Explainability, Audit. Every number comes
from the API; provenance tags distinguish SIMULATED / TEST MODE / LIVE PROVIDER, RECONCILED and
MODEL ESTIMATE values. Financial actions require confirmation and wait for the server.
Tokens are kept in `sessionStorage` for the current tab only.

Note: `package-lock.json` is not committed (built offline from React 19.2.5 / esbuild 0.27.7);
run `npm install` with network access to generate it.

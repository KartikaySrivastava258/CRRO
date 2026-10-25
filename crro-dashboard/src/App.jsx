import { useEffect, useState } from "react";
import { api, auth } from "./lib/api.js";
import { useOnline, useResource, useRoute } from "./lib/hooks.js";
import { Button, Card, Tag } from "./components/ui.jsx";
import Overview from "./pages/Overview.jsx";
import Cases from "./pages/Cases.jsx";
import CaseDetail from "./pages/CaseDetail.jsx";
import Optimizer from "./pages/Optimizer.jsx";
import Approvals from "./pages/Approvals.jsx";
import Audit from "./pages/Audit.jsx";
import Explainability from "./pages/Explainability.jsx";

const NAV = [["/", "Overview"], ["/cases", "Cases"], ["/approvals", "Approvals"], ["/optimizer", "Optimizer"],
             ["/explain", "Explainability"], ["/audit", "Audit"]];

function SignIn({ onDone }) {
  const ready = useResource(() => api.ready(), []);
  const [token, setToken] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const demo = ready.data?.provider_mode === "SIMULATOR";
  const submit = async (t) => {
    setBusy(true); setError(null); auth.set(t);
    try { onDone(await api.me()); } catch (e) { auth.clear(); setError(e.message); } finally { setBusy(false); }
  };
  return (
    <main className="login">
      <Card title="CRRO · Recovery Operations" sub="Operator sign-in. Tokens are kept in this tab's session storage only.">
        <form onSubmit={(e) => { e.preventDefault(); submit(token.trim()); }} className="grid">
          <label className="field"><span>Operator token</span>
            <input type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} required /></label>
          {error && <div className="inline-error" role="alert">{error}</div>}
          <Button busy={busy} variant="btn-primary" type="submit">Sign in</Button>
          {demo && (
            <div className="callout">Local demo detected (simulated provider). Documented demo credentials:
              <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
                <button type="button" className="btn btn-sm" onClick={() => submit("demo-operator-token-local-only")}>Use demo operator</button>
                <button type="button" className="btn btn-sm" onClick={() => submit("demo-viewer-token-local-only")}>Use read-only viewer</button>
              </div>
            </div>)}
          {ready.error && <div className="inline-error">API not reachable: {ready.error.message}</div>}
        </form>
      </Card>
    </main>
  );
}

export default function App() {
  const [me, setMe] = useState(null);
  const [checked, setChecked] = useState(false);
  const route = useRoute();
  const online = useOnline();
  useEffect(() => {
    const out = () => setMe(null);
    window.addEventListener("crro:unauthorized", out);
    if (auth.get()) api.me().then(setMe).catch(() => auth.clear()).finally(() => setChecked(true));
    else setChecked(true);
    return () => window.removeEventListener("crro:unauthorized", out);
  }, []);
  const ov = useResource(() => (me ? api.overview() : Promise.resolve(null)), [me, route.path], {});
  if (!checked) return null;
  if (!me) return <SignIn onDone={setMe} />;
  const [section, sub] = route.parts;
  const active = "/" + (section || "");
  const pending = ov.data?.attention?.pending_approvals;
  let page;
  if (!section) page = <Overview />;
  else if (section === "cases" && sub) page = <CaseDetail id={sub} me={me} />;
  else if (section === "cases") page = <Cases query={route.query} />;
  else if (section === "approvals") page = <Approvals me={me} />;
  else if (section === "optimizer") page = <Optimizer me={me} />;
  else if (section === "explain") page = <Explainability caseId={route.query.case} />;
  else if (section === "audit") page = <Audit query={route.query} />;
  else page = <section className="page"><h1>Not found</h1><a href="#/">Back to overview</a></section>;
  return (
    <div className="shell">
      <a className="skip" href="#main">Skip to content</a>
      <aside className="side">
        <div className="brand"><strong>CRRO</strong><small>Counterfactual Revenue Recovery Optimizer</small></div>
        <nav className="nav" aria-label="Primary">
          {NAV.map(([to, label]) => (
            <a key={to} href={"#" + to} aria-current={active === to ? "page" : undefined}>
              {label}{to === "/approvals" && pending > 0 && <span className="count" aria-label={`${pending} pending`}>{pending}</span>}
            </a>))}
        </nav>
        <div className="side-foot">Research prototype. Not affiliated with any payment network. No real card data.</div>
      </aside>
      <div className="main">
        <div className="envbar" role="note">
          <Tag kind="SYNTHETIC" /><span>Synthetic demo data</span>
          <Tag kind={me.provider_mode} /><span>Provider: {me.provider_mode === "SIMULATOR" ? "local simulator" : me.provider_mode}</span>
          <span className="mono muted">model {me.model_version}</span>
          {!online && <span className="warn" role="alert">Offline — showing last loaded data</span>}
          <span className="spacer" />
          <span>{me.actor} · {me.merchant_id} · {me.role}</span>
          <button className="btn btn-sm" onClick={() => { auth.clear(); setMe(null); }}>Sign out</button>
        </div>
        <main id="main">{page}</main>
      </div>
    </div>
  );
}

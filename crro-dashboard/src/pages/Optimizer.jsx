import { useEffect, useState } from "react";
import { api, download } from "../lib/api.js";
import { human, int, money, moneyCompact, pct, when } from "../lib/format.js";
import { useResource } from "../lib/hooks.js";
import { Button, Card, ErrorBox, KV, Page, Resource, Status, Table, Tag } from "../components/ui.jsx";
import { BarChart, LineChart } from "../components/charts.jsx";

const FIELDS = [
  ["n_cases", "Synthetic cases", "count", 200, 5000, 1500, 1],
  ["seed", "Random seed", "integer", 0, 1000000, 2026, 1],
  ["contact_budget", "Contact budget", "contacts / day", 0, 100000, 150, 1],
  ["incentive_budget_inr", "Incentive budget", "₹ exposure / day", 0, 10000000, 7500, 0.01],
  ["cost_multiplier", "Action-cost multiplier", "× registry cost", 0, 20, 1, 0.1],
];

export default function Optimizer({ me }) {
  const runs = useResource(() => api.runs(), []);
  const [form, setForm] = useState(Object.fromEntries(FIELDS.map((f) => [f[0], String(f[5])])));
  const [errors, setErrors] = useState({});
  const [busy, setBusy] = useState(false); const [elapsed, setElapsed] = useState(0);
  const [runErr, setRunErr] = useState(null); const [selected, setSelected] = useState(null);
  const run = useResource(() => (selected ? api.run(selected) : Promise.resolve(null)), [selected]);
  useEffect(() => { if (!selected && runs.data?.items?.[0]) setSelected(runs.data.items[0].run_id); }, [runs.data, selected]);
  useEffect(() => { if (!busy) return undefined; const t0 = Date.now(); const t = setInterval(() => setElapsed((Date.now() - t0) / 1000), 200); return () => clearInterval(t); }, [busy]);
  const validate = () => {
    const e = {}, out = {};
    for (const [k, , , lo, hi, , step] of FIELDS) {
      const v = Number(form[k]);
      if (form[k] === "" || !Number.isFinite(v)) e[k] = "Required number";
      else if (v < lo || v > hi) e[k] = `Must be between ${lo} and ${hi}`;
      else if (step === 1 && !Number.isInteger(v)) e[k] = "Must be a whole number";
      out[k] = v;
    }
    setErrors(e); return Object.keys(e).length ? null : out;
  };
  const submit = async (ev) => {
    ev.preventDefault(); if (busy) return;
    const cfg = validate(); if (!cfg) return;
    setBusy(true); setRunErr(null);
    try { const r = await api.startRun(cfg); setSelected(r.run_id); runs.reload(); } catch (e) { setRunErr(e); } finally { setBusy(false); }
  };
  return (
    <Page eyebrow="Decision science" title="Portfolio optimizer"
          intro="Runs every policy on the same freshly generated synthetic cases, budgets and random draws, using the live model. Outcomes come from the simulator's hidden ground truth. These are simulations, not observed recoveries.">
      <Card title="Run a simulation" right={<Tag kind="SYNTHETIC" />}>
        <form onSubmit={submit} noValidate>
          <div className="form-grid">
            {FIELDS.map(([k, label, unit, lo, hi, , step]) => (
              <label key={k} className="field" data-invalid={!!errors[k]}>
                <span>{label} <small>({unit})</small></span>
                <input type="number" inputMode="decimal" min={lo} max={hi} step={step} value={form[k]} aria-invalid={!!errors[k]}
                       aria-describedby={errors[k] ? `${k}-err` : undefined} onChange={(e) => setForm({ ...form, [k]: e.target.value })} />
                {errors[k] && <small id={`${k}-err`} className="inline-error">{errors[k]}</small>}
              </label>))}
            <Button busy={busy} variant="btn-primary" type="submit" disabled={me.role !== "operator"}>
              {busy ? `Running… ${elapsed.toFixed(0)}s` : "Run simulation"}</Button>
          </div>
          {me.role !== "operator" && <p className="card-sub">Operator role required to start runs.</p>}
        </form>
        {runErr && <div style={{ marginTop: 12 }}><ErrorBox error={runErr} /></div>}
      </Card>
      <div className="grid" style={{ marginTop: 14 }}>
        <Resource res={run} label="Loading run">{(r) => r ? <RunResult r={r} /> : null}</Resource>
        <Card title="Recent runs" id="runs">
          <Resource res={runs}>{(d) => (
            <Table caption="Optimizer runs" rows={d.items} rowKey={(x) => x.run_id} onRowClick={(x) => setSelected(x.run_id)} empty="No runs yet"
              columns={[{ key: "id", label: "Run", render: (x) => <span className="mono" style={{ color: x.run_id === selected ? "var(--accent)" : undefined }}>{x.run_id}</span> },
                { key: "s", label: "Status", render: (x) => <Status value={x.status} /> }, { key: "seed", label: "Seed", num: true, render: (x) => x.seed },
                { key: "n", label: "Cases", num: true, render: (x) => int(x.config.n_cases) },
                { key: "c", label: "Contacts", num: true, render: (x) => int(x.config.contact_budget) },
                { key: "d", label: "Duration", num: true, render: (x) => x.duration_ms ? `${(x.duration_ms / 1000).toFixed(1)} s` : "—" },
                { key: "t", label: "Created", render: (x) => when(x.created_at) }]} />)}
          </Resource>
        </Card>
      </div>
    </Page>
  );
}

function RunResult({ r }) {
  const res = r.result;
  if (!res) return <Card title={`Run ${r.run_id}`}><Status value={r.status} /> {r.error}</Card>;
  const pol = res.policies;
  const ev = res.exact_vs_greedy, bind = res.binding_constraints;
  const deploy = pol.filter((p) => !p.policy.includes("diagnostic") && !p.policy.includes("simulator-only") && p.policy !== "CONTROL");
  const best = deploy.reduce((a, b) => (b.incremental_expected_net_paise > a.incremental_expected_net_paise ? b : a), deploy[0]);
  return (
    <>
      <Card title={`Run ${r.run_id}`} sub={`seed ${r.seed} · ${int(r.config.n_cases)} cases · model ${res.model_version}`}
            right={<span style={{ display: "flex", gap: 6 }}>
              <button className="btn btn-sm" onClick={() => download(api.runCsv(r.run_id), `${r.run_id}-policies.csv`)}>CSV</button>
              <button className="btn btn-sm" onClick={() => { const b = new Blob([JSON.stringify(r, null, 1)], { type: "application/json" });
                const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(b), download: `${r.run_id}.json` }); a.click(); }}>JSON</button></span>}>
        <div className="callout" style={{ marginBottom: 12 }}>Best deployable policy this run: <strong className="mono">{best.policy}</strong>. Single-seed result — see the multi-seed benchmark for confidence intervals.</div>
        <BarChart label="Expected incremental net value vs no-action (simulator expectation)" unit="₹" format={(v) => moneyCompact(v)}
          highlight="CRRO_EXACT" note="grey = not deployable (diagnostic / simulator-only ceilings)"
          data={pol.filter((p) => p.policy !== "CONTROL").map((p) => ({ label: p.policy, value: p.incremental_expected_net_paise,
            muted: p.policy.includes("diagnostic") || p.policy.includes("simulator-only") }))} />
        <div style={{ marginTop: 14 }}>
          <Table caption="Policy comparison" rows={pol} rowKey={(p) => p.policy} columns={[
            { key: "p", label: "Policy", render: (p) => <>{p.policy}{!p.constrained && <div className="muted">ignores budgets</div>}</> },
            { key: "e", label: "Incr. net (expected)", num: true, render: (p) => money(p.incremental_expected_net_paise, { whole: true }) },
            { key: "r", label: "Incr. net (realised draw)", num: true, render: (p) => money(p.incremental_net_paise, { whole: true }) },
            { key: "rr", label: "Recovery rate", num: true, render: (p) => pct(p.recovery_rate) },
            { key: "a", label: "Actions", num: true, render: (p) => int(p.actions_taken) },
            { key: "c", label: "Contacts", num: true, render: (p) => int(p.contacts_used) },
            { key: "i", label: "Incentive exposure", num: true, render: (p) => money(p.incentive_exposure_paise, { whole: true }) },
            { key: "v", label: "Constraint violations", num: true, render: (p) => p.feasibility_violations === null ? "n/a" : p.feasibility_violations.length }]} />
        </div>
      </Card>
      <div className="grid cols-3">
        <Card title="Exact vs greedy" sub="Same scored candidates and budgets. Objective = model-estimated value.">
          <KV items={[["Exact solver", ev.exact_solver], ["Solver status", <Status value={ev.exact_status} />],
            ["Exact objective", money(ev.exact_objective_paise, { whole: true })], ["Greedy objective", money(ev.greedy_objective_paise, { whole: true })],
            ["Exact gain", ev.greedy_objective_paise ? pct(ev.exact_objective_paise / ev.greedy_objective_paise - 1, 2) : "—"],
            ["Runtime", `${ev.exact_wall_ms.toFixed(0)} ms vs ${ev.greedy_wall_ms.toFixed(0)} ms`]]} />
        </Card>
        <Card title="Binding constraints" sub="Binding = at least one positive-value case was left untreated because of it.">
          <ul className="attention">{Object.entries(bind).map(([k, v]) => (
            <li key={k}><span>{human(k)}</span><span className={"n" + (v.binding ? " hot" : "")}>{v.binding ? "binding" : "slack"}</span></li>))}</ul>
        </Card>
        <Card title="Why cases stayed at NO_ACTION" sub="CRRO exact allocation.">
          <ul className="attention">{Object.entries(res.crro_no_action_reasons).map(([k, n]) => (
            <li key={k}><span>{human(k)}</span><span className="n">{int(n)}</span></li>))}
            {Object.entries(res.crro_blocked_by).slice(0, 4).map(([k, n]) => (
              <li key={k}><span className="muted mono" style={{ fontSize: 12 }}>{k}</span><span className="n">{int(n)}</span></li>))}</ul>
        </Card>
      </div>
      <Card title="Contact-budget sensitivity" sub="Same cases; CRRO exact re-solved at 0.25×–2× the contact budget. Diminishing returns show where the next contact stops paying.">
        <LineChart xLabel="contact budget (contacts/day)" yLabel="expected incremental net (₹)" formatX={(x) => int(x)} formatY={(y) => moneyCompact(y)}
          points={res.contact_budget_sweep.map((p) => ({ x: p.contact_budget, y: p.incremental_expected_net_paise }))} />
      </Card>
    </>
  );
}

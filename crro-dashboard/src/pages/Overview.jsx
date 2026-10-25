import { api } from "../lib/api.js";
import { ago, int, money, pct, when } from "../lib/format.js";
import { useResource } from "../lib/hooks.js";
import { Card, Empty, Kpi, Page, Resource, Tag } from "../components/ui.jsx";
import { Funnel, Meter } from "../components/charts.jsx";

const sumModes = (obj, key) => Object.values(obj || {}).reduce((a, v) => a + (v[key] || 0), 0);

export default function Overview() {
  const ov = useResource(() => api.overview(), [], { pollMs: 30000 });
  const runs = useResource(() => api.runs(), []);
  const bench = useResource(() => api.benchmark().catch((e) => (e.status === 404 ? null : Promise.reject(e))), []);
  return (
    <Page eyebrow="Portfolio" title="Recovery overview"
          intro="Every figure on this page is computed from the ledger. Confirmed recoveries require a signature-verified provider event whose amount and currency matched; model expectations are labelled separately."
          actions={<button className="btn" onClick={ov.reload} aria-busy={ov.loading}>Refresh</button>}>
      <Resource res={ov} label="Loading portfolio">{(d) => <Body d={d} runs={runs} bench={bench} />}</Resource>
    </Page>
  );
}

function Body({ d, runs, bench }) {
  const rec = d.confirmed_recovered_by_provider_mode;
  const gross = sumModes(rec, "gross_recovered_paise"), cash = sumModes(rec, "cash_collected_paise");
  const costs = sumModes(d.action_costs_by_provider_mode, "action_cost_paise");
  const modes = Object.keys(rec);
  const a = d.attention, b = d.budget_today, f = d.funnel;
  const statusLink = (s) => `#/cases?status=${s}`;
  return (
    <>
      <div className="grid kpis">
        <Kpi label="Open exposure" value={money(d.open_exposure.amount_paise, { whole: true })}
             sub={`${int(d.open_exposure.cases)} cases not yet resolved`} href={statusLink(d.open_exposure.statuses.join(","))} />
        <Kpi label="Confirmed recovered (gross)" value={money(gross, { whole: true })} tone={gross ? "ok" : ""}
             tag={<span style={{ display: "flex", gap: 4 }}><Tag kind="RECONCILED" />{modes.map((m) => <Tag key={m} kind={m} />)}</span>}
             sub={`${int(f.recovered)} cases · cash collected ${money(cash, { whole: true })}`} href={statusLink("RECOVERED")} />
        <Kpi label="Net of incentives & action costs" value={money(cash - costs, { whole: true })}
             sub={`incentives ${money(gross - cash, { whole: true })} · action costs ${money(costs, { whole: true })}`}
             tag={modes.map((m) => <Tag key={m} kind={m} />)} />
        <Kpi label="Expected incremental value (open)" value={money(d.expected_incremental_value_open_decisions.expected_value_paise, { whole: true })}
             tag={<Tag kind="ESTIMATE" />} sub={`${int(d.expected_incremental_value_open_decisions.decisions)} open decisions vs no-action`} />
        <Kpi label="Awaiting approval" value={int(a.pending_approvals)} tone={a.pending_approvals ? "warn" : ""}
             sub={a.oldest_pending_approval_at ? `oldest ${ago(a.oldest_pending_approval_at)}` : "queue empty"} href="#/approvals" />
      </div>
      <div className="grid cols-3">
        <Card title="Needs attention" sub="Items that cannot resolve without an operator or a provider event." id="att">
          <ul className="attention">
            {[["Provider outcome unknown (timeout)", a.outcome_unknown, statusLink("OUTCOME_UNKNOWN")],
              ["Reconciliation mismatch (needs review)", a.needs_review, statusLink("NEEDS_REVIEW")],
              ["Review flags on recovered cases", a.review_flags, "#/cases?status=RECOVERED"],
              ["Provider errors", a.action_failed, statusLink("ACTION_FAILED")],
              ["Unmatched provider events", a.unmatched_provider_events, "#/audit?view=unmatched"],
              ["Rejected webhooks (24h, bad signature/size)", a.webhook_rejections_24h, null],
              ["Pending approvals", a.pending_approvals, "#/approvals"]].map(([label, n, href]) => (
              <li key={label}><span>{href ? <a href={href}>{label}</a> : label}</span>
                <span className={"n" + (n > 0 ? " hot" : "")}>{int(n)}</span></li>))}
          </ul>
        </Card>
        <Card title="Recovery funnel" sub="Case counts, all time, this merchant." id="fun">
          <Funnel steps={[{ label: "Payment failed", value: f.failed }, { label: "Decided", value: f.decided },
            { label: "Action recommended", value: f.recommended }, { label: "Approved / auto-approved", value: f.approved },
            { label: "Executed (provider accepted)", value: f.executed }, { label: "Provider-confirmed recovered", value: f.recovered }]} />
        </Card>
        <Card title="Today's capacity" sub={`UTC day ${b.day_utc} · policy ${d.policy_version}`} id="cap">
          <Meter label="Customer contacts" used={b.contacts_used} limit={b.contact_budget} format={int} />
          <Meter label="Incentive exposure committed" used={b.incentive_committed_paise} limit={b.incentive_budget_paise}
                 format={(v) => money(v, { whole: true })} />
          <p className="card-sub" style={{ marginTop: 14 }}>Reservations are atomic in the database; failed provider calls release them.</p>
        </Card>
      </div>
      <div className="grid cols-2" style={{ marginTop: 14 }}>
        <Card title="Cases by status" id="sts" right={<a href="#/cases">All cases →</a>}>
          {Object.keys(d.status_breakdown).length ? (
            <div className="table-wrap"><table><thead><tr><th>Status</th><th className="num">Cases</th><th className="num">Amount</th></tr></thead>
              <tbody>{Object.entries(d.status_breakdown).sort((x, y) => y[1].count - x[1].count).map(([s, v]) => (
                <tr key={s}><td><a href={statusLink(s)}>{s.replaceAll("_", " ")}</a></td><td className="num">{int(v.count)}</td>
                  <td className="num">{money(v.amount_paise, { whole: true })}</td></tr>))}</tbody></table></div>)
            : <Empty title="No cases yet">Send a payment.failed event or run scripts/seed_demo.py.</Empty>}
        </Card>
        <Card title="Decision evidence (offline)" sub="Synthetic simulation results — not merchant revenue." id="ev"
              right={<Tag kind="SYNTHETIC" />}>
          <Resource res={bench}>{(bm) => bm ? <BenchSummary bm={bm} /> : <Empty title="No multi-seed benchmark found" />}</Resource>
          <Resource res={runs}>{(r) => r.items.length ? (
            <p className="card-sub" style={{ marginTop: 12 }}>Latest optimizer run <a href="#/optimizer">{r.items[0].run_id}</a> · seed {r.items[0].seed} · {when(r.items[0].created_at)}</p>) : null}</Resource>
        </Card>
      </div>
    </>
  );
}

function BenchSummary({ bm }) {
  const p = bm.policies, c = p.CRRO_EXACT?.incremental_expected_net_paise;
  const diff = bm.paired_differences_crro_exact_minus;
  const share = bm.crro_share_of_constrained_oracle;
  if (!c) return <Empty title="Benchmark summary incomplete" />;
  return (
    <div className="grid">
      <p className="card-sub" style={{ margin: 0 }}>{bm.meta.seeds.length} seeds × {int(bm.meta.n_test)} held-out, distribution-shifted cases; identical budgets per policy; 95% t-intervals.</p>
      <div className="table-wrap"><table><thead><tr><th>Comparison (expected incremental net)</th><th className="num">Mean</th><th className="num">95% CI</th></tr></thead><tbody>
        <tr><td>CRRO exact vs no-action</td><td className="num">{money(c.mean, { whole: true })}</td><td className="num">{money(c.ci95_low, { whole: true })} – {money(c.ci95_high, { whole: true })}</td></tr>
        {["RULE_BASED", "RAW_PROBABILITY", "UPLIFT_GREEDY"].map((k) => diff[k] && (
          <tr key={k}><td>CRRO exact − {k.replaceAll("_", " ").toLowerCase()}</td><td className="num">{money(diff[k].mean, { whole: true })}</td>
            <td className="num">{money(diff[k].ci95_low, { whole: true })} – {money(diff[k].ci95_high, { whole: true })}</td></tr>))}
        <tr><td>Share of budget-constrained oracle</td><td className="num">{pct(share.mean)}</td><td className="num">{pct(share.ci95_low)} – {pct(share.ci95_high)}</td></tr>
      </tbody></table></div>
    </div>
  );
}

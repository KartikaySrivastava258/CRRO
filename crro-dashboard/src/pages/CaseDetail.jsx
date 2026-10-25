import { useState } from "react";
import { api } from "../lib/api.js";
import { human, money, pct, pp, short, when } from "../lib/format.js";
import { useResource } from "../lib/hooks.js";
import { Button, Card, ConfirmDialog, KV, Page, Resource, Status, Table, Tag } from "../components/ui.jsx";

export default function CaseDetail({ id, me }) {
  const res = useResource(() => api.case(id), [id]);
  return (
    <Page eyebrow={<a href="#/cases">← Cases</a>} title={res.data ? `Case ${res.data.case.external_payment_id}` : "Case"}
          actions={<><a className="btn" href={`#/explain?case=${id}`}>Explain decision</a><button className="btn" onClick={res.reload}>Refresh</button></>}>
      <Resource res={res} label="Loading case">{(d) => <Detail d={d} me={me} reload={res.reload} />}</Resource>
    </Page>
  );
}

export function CandidateTable({ decision }) {
  return (
    <Table caption="Candidate actions" rowKey={(c) => c.action}
      rows={[...decision.candidates].sort((a, b) => (b.expected_value_paise ?? -1e18) - (a.expected_value_paise ?? -1e18))}
      columns={[
        { key: "action", label: "Action", render: (c) => <strong style={{ color: c.action === decision.selected_action ? "var(--accent)" : undefined }}>{human(c.action)}{c.action === decision.selected_action && " ✓"}</strong> },
        { key: "p", label: "P(recover | action)", num: true, render: (c) => pct(c.p_action) },
        { key: "u", label: "Uplift vs no action", num: true, render: (c) => pp(c.uplift) },
        { key: "c", label: "Action cost", num: true, render: (c) => money(c.action_cost_paise) },
        { key: "d", label: "Discount if paid", num: true, render: (c) => c.incentive_discount_paise ? money(c.incentive_discount_paise) : "—" },
        { key: "ev", label: "Expected incremental net", num: true, render: (c) => money(c.expected_value_paise) },
        { key: "r", label: "Feasibility", render: (c) => c.feasible ? <span className="status status-ok">feasible</span>
            : <span className="reasons">{c.rejection_reasons.map((r) => <span key={r} className="reason">{r}</span>)}</span> },
      ]} />
  );
}

function Detail({ d, me, reload }) {
  const { case: c, decision: dec, approval: apr, attempt: att, recovery: rec } = d;
  const [dlg, setDlg] = useState(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const canOperate = me.role === "operator";
  const simulate = async (event_type, deliveries = 1) => {
    const r = await api.simulate(att.provider_reference, { event_type, deliveries });
    setMsg(`Simulator delivered ${event_type} ${deliveries}×: ` + r.deliveries.map((x) => x.body.outcome || x.body.status).join(", "));
    setDlg(null); reload();
  };
  const resolve = async () => {
    setBusy(true); setMsg(null);
    try { const r = await api.resolve(att.attempt_id); setMsg(`Resolution: attempt is now ${r.status}`); reload(); }
    catch (e) { setMsg(`Resolve failed: ${e.message}`); } finally { setBusy(false); }
  };
  return (
    <div className="grid">
      {msg && <div className="callout" role="status">{msg}</div>}
      <Card title="Case" right={<span style={{ display: "flex", gap: 6 }}><Status value={c.status} /><Tag kind="SYNTHETIC" /></span>}>
        <KV items={[["Amount", money(c.amount_paise)], ["Currency", c.currency], ["Failure reason", human(c.failure_reason)],
          ["Payment method", c.payment_method], ["Payment attempts", c.attempt_count], ["Customer (pseudonymous)", <span className="mono">{c.customer_ref}</span>],
          ["Case id", <span className="mono">{c.case_id}</span>], ["Correlation id", <a className="mono" href={`#/audit?correlation_id=${c.correlation_id}`}>{c.correlation_id}</a>],
          ["Failed at", when(c.failed_at)], ["Status reason", c.status_reason || "—"],
          c.review_reason && ["Review flag", <span className="status status-bad">{c.review_reason}</span>], ["Data origin", c.data_origin]]} />
      </Card>
      {dec && (
        <Card title="Decision" sub="Model output is advisory; deterministic guardrails decide feasibility." right={<Tag kind="ESTIMATE" />}>
          <KV items={[["Selected action", <strong>{human(dec.selected_action)}</strong>], ["Reason", dec.reason_code || "—"],
            ["P(recover | no action)", pct(dec.p_control)], ["P(recover | selected)", pct(dec.p_selected)],
            ["Expected incremental net", money(dec.expected_value_paise)], ["Approval required", dec.requires_approval ? "yes" : "no"],
            ["Model version", <span className="mono">{dec.model_version}</span>], ["Policy version", <span className="mono">{dec.policy_version}</span>]]} />
          {dec.feature_imputations?.length > 0 && (
            <div className="callout warn" style={{ marginTop: 12 }}>Imputed or adjusted inputs: {dec.feature_imputations.map((i) => `${i.feature} (${i.reason.replaceAll("_", " ")})`).join("; ")}</div>)}
          <div style={{ marginTop: 12 }}><CandidateTable decision={dec} /></div>
        </Card>)}
      <div className="grid cols-2">
        {apr && (
          <Card title="Approval" right={<Status value={apr.status} />}>
            <KV items={[["Action", human(apr.action)], ["Amount", money(apr.amount_paise)], ["Threshold", money(apr.threshold_paise)],
              ["Why", apr.reason], ["Decided by", apr.decided_by || "—"], ["Note", apr.decision_note || "—"],
              ["Decided at", when(apr.decided_at)], ["Expires", when(apr.expires_at)]]} />
            {apr.status === "PENDING" && <p style={{ marginTop: 10 }}><a href="#/approvals">Decide in the approval queue →</a></p>}
          </Card>)}
        {att && (
          <Card title="Provider attempt" right={<span style={{ display: "flex", gap: 6 }}><Status value={att.status} /><Tag kind={att.provider_mode} /></span>}
                sub="An accepted attempt means a link/retry was created — not that money was recovered.">
            <KV items={[["Action", human(att.action)], ["Collect amount", money(att.collect_amount_paise)],
              ["Discount if paid", money(att.discount_paise)], ["Action cost", money(att.action_cost_paise)],
              ["Idempotency key", <span className="mono">{att.idempotency_key}</span>], ["Provider reference", <span className="mono">{att.provider_reference || "—"}</span>],
              ["Provider status", att.provider_status || "—"], ["Dispatch count", att.dispatch_count], ["Last error", att.last_error || "—"]]} />
            <div className="page-actions" style={{ marginTop: 12 }}>
              {att.status === "UNKNOWN" && <Button busy={busy} disabled={!canOperate} onClick={resolve}
                title={canOperate ? "Look up the idempotency key at the provider" : "Operator role required"}>Resolve unknown outcome</Button>}
              {me.simulator_endpoints && att.provider_reference && !["RECOVERED"].includes(c.status) && (<>
                <button className="btn" disabled={!canOperate} onClick={() => setDlg("paid")}>Simulate customer payment</button>
                <button className="btn" disabled={!canOperate} onClick={() => setDlg("expired")}>Simulate link expiry</button></>)}
              {me.simulator_endpoints && att.provider_reference && c.status === "RECOVERED" && (
                <button className="btn" disabled={!canOperate} onClick={() => setDlg("dup")}>Redeliver payment webhook ×3</button>)}
            </div>
          </Card>)}
        {rec && (
          <Card title="Reconciled recovery" right={<span style={{ display: "flex", gap: 6 }}><Tag kind="RECONCILED" /><Tag kind={rec.provider_mode} /></span>}>
            <KV items={[["Gross recovered", money(rec.gross_recovered_paise)], ["Cash collected", money(rec.cash_collected_paise)],
              ["Incentive conceded", money(rec.incentive_cost_paise)], ["Action cost", money(rec.action_cost_paise)],
              ["Provider payment", <span className="mono">{rec.provider_payment_id || "—"}</span>], ["Provider event", <span className="mono">{rec.provider_event_id}</span>],
              ["Confirmed at", when(rec.confirmed_at)]]} />
          </Card>)}
      </div>
      <Card title="Timeline" sub="Append-only audit log for this case (oldest first).">
        <ol className="timeline">
          {d.timeline.map((t) => (
            <li key={t.seq}>
              <div className="t-head"><span className="t-type">{human(t.event_type)}</span><span className="t-meta">{when(t.ts)} · {t.actor}</span></div>
              <div className="t-data">{summarize(t)}</div>
            </li>))}
        </ol>
      </Card>
      {d.provider_events.length > 0 && (
        <Card title="Provider events" sub="Signature-verified webhooks matched to this case.">
          <Table caption="Provider events" rows={d.provider_events} rowKey={(e) => e.provider_event_id} columns={[
            { key: "id", label: "Event id", render: (e) => <span className="mono">{short(e.provider_event_id, 16)}</span> },
            { key: "t", label: "Type", render: (e) => e.event_type },
            { key: "a", label: "Amount", num: true, render: (e) => money(e.summary?.amount_paise) },
            { key: "o", label: "Outcome", render: (e) => <><Status value={e.outcome} />{e.outcome_reason && <div className="muted">{e.outcome_reason}</div>}</> },
            { key: "r", label: "Received", render: (e) => when(e.received_at) }]} />
        </Card>)}
      <ConfirmDialog open={!!dlg} title={{ paid: "Simulate customer payment", expired: "Simulate link expiry", dup: "Redeliver webhook" }[dlg]}
        confirmLabel="Send signed webhook" onCancel={() => setDlg(null)}
        onConfirm={() => simulate(dlg === "expired" ? "payment_link.expired" : att.action.startsWith("RETRY") ? "payment.captured" : "payment_link.paid", dlg === "dup" ? 3 : 1)}>
        The simulator signs a Razorpay-shaped event with the configured secret and posts it through the real webhook endpoint
        (signature check, deduplication, amount/currency validation). Labelled SIMULATED — no money moves.
      </ConfirmDialog>
    </div>
  );
}

function summarize(t) {
  const x = t.data || {};
  switch (t.event_type) {
    case "PAYMENT_FAILED_RECEIVED": return `${money(x.amount_paise)} ${x.currency} · ${x.failure_reason} via ${x.payment_method}`;
    case "DECISION_RECORDED": return `${x.selected_action} (${x.reason_code || "—"}) · expected ${money(x.expected_value_paise)} · ${x.model_version}`;
    case "CASE_STATUS_CHANGED": return `${x.from} → ${x.to}${x.reason ? ` (${x.reason})` : ""}${x.provider_event_id ? ` · event ${x.provider_event_id}` : ""}`;
    case "ACTION_QUEUED": return `${x.action} · collect ${money(x.collect_amount_paise)} · key ${x.idempotency_key}`;
    case "ACTION_DISPATCH_RESULT": return `${x.provider_result}${x.provider_reference ? ` · ${x.provider_reference}` : ""}${x.provider_dedup_hit ? " · provider dedup hit" : ""}${x.detail ? ` · ${x.detail}` : ""}`;
    case "APPROVAL_APPROVED": case "APPROVAL_REJECTED": return `${x.action} · “${x.note || ""}”`;
    case "AMOUNT_MISMATCH": return `expected ${money(x.expected_paise)}, provider reported ${money(x.observed_paise)}`;
    default: return Object.entries(x).filter(([, v]) => v !== null && typeof v !== "object").map(([k, v]) => `${k}=${v}`).join(" · ");
  }
}

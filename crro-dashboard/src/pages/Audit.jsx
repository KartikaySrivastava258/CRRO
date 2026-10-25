import { useEffect, useState } from "react";
import { api, download } from "../lib/api.js";
import { money, short, when } from "../lib/format.js";
import { go, useResource } from "../lib/hooks.js";
import { Button, Card, ErrorBox, Loading, Page, Resource, Table } from "../components/ui.jsx";

const TYPES = ["", "PAYMENT_FAILED_RECEIVED", "DECISION_RECORDED", "APPROVAL_REQUESTED", "APPROVAL_APPROVED", "APPROVAL_REJECTED",
  "APPROVAL_EXPIRED", "ACTION_QUEUED", "ACTION_DISPATCH_STARTED", "ACTION_DISPATCH_RESULT", "ACTION_REQUEUED_AFTER_LOOKUP",
  "CASE_STATUS_CHANGED", "AMOUNT_MISMATCH", "REVIEW_FLAG_SET", "CUSTOMER_PAYMENT_ATTEMPT_FAILED", "CUSTOMER_OPT_OUT_SET",
  "POLICY_UPDATED", "OPTIMIZER_RUN_COMPLETED"];

export default function Audit({ query }) {
  const [f, setF] = useState({ case_id: "", event_type: "", correlation_id: "", from: "", to: "" });
  useEffect(() => setF({ case_id: query.case_id || "", event_type: query.event_type || "", correlation_id: query.correlation_id || "",
    from: query.from || "", to: query.to || "" }), [query.case_id, query.event_type, query.correlation_id, query.from, query.to]);
  const params = { case_id: query.case_id, event_type: query.event_type, correlation_id: query.correlation_id, from: query.from, to: query.to };
  const res = useResource(() => api.audit({ ...params, limit: 50 }), [JSON.stringify(params)]);
  const unmatched = useResource(() => api.unmatched(), []);
  const [items, setItems] = useState([]); const [cursor, setCursor] = useState(null);
  const [more, setMore] = useState(false); const [exporting, setExporting] = useState(false); const [err, setErr] = useState(null);
  useEffect(() => { if (res.data) { setItems(res.data.items); setCursor(res.data.next_cursor); } }, [res.data]);
  const toIso = (v) => (v ? new Date(v).toISOString() : "");
  return (
    <Page eyebrow="Evidence" title="Audit log"
          intro="Append-only (database triggers reject UPDATE/DELETE). Follow a correlation id from failure to decision, provider call, webhook and reconciliation. Not tamper-evident (no hash chain)."
          actions={<Button busy={exporting} onClick={async () => { setExporting(true); setErr(null);
            try { await download(api.auditExport(params), "crro-audit-export.json"); } catch (e) { setErr(e); } finally { setExporting(false); } }}>Export JSON</Button>}>
      {err && <ErrorBox error={err} />}
      <form className="filters" onSubmit={(e) => { e.preventDefault(); go("/audit", { ...f, from: toIso(f.from), to: toIso(f.to) }); }}>
        <label className="field"><span>Case id</span><input value={f.case_id} onChange={(e) => setF({ ...f, case_id: e.target.value })} /></label>
        <label className="field"><span>Event type</span><select value={f.event_type} onChange={(e) => setF({ ...f, event_type: e.target.value })}>
          {TYPES.map((t) => <option key={t} value={t}>{t || "All types"}</option>)}</select></label>
        <label className="field"><span>Correlation id</span><input value={f.correlation_id} onChange={(e) => setF({ ...f, correlation_id: e.target.value })} /></label>
        <label className="field"><span>From (local time)</span><input type="datetime-local" value={f.from ? f.from.slice(0, 16) : ""} onChange={(e) => setF({ ...f, from: e.target.value })} /></label>
        <label className="field"><span>To (local time)</span><input type="datetime-local" value={f.to ? f.to.slice(0, 16) : ""} onChange={(e) => setF({ ...f, to: e.target.value })} /></label>
        <Button type="submit">Apply</Button>
        <button type="button" className="btn" onClick={() => go("/audit")}>Clear</button>
      </form>
      {res.loading && !items.length ? <Loading /> : res.error ? <ErrorBox error={res.error} onRetry={res.reload} /> : (
        <>
          <Table caption="Audit events" rows={items} rowKey={(r) => r.audit_id} empty="No audit events match"
            columns={[
              { key: "ts", label: "Time", render: (r) => <span className="muted">{when(r.ts)}</span> },
              { key: "t", label: "Event", render: (r) => <strong>{r.event_type}</strong> },
              { key: "a", label: "Actor", render: (r) => r.actor },
              { key: "c", label: "Case", render: (r) => r.case_id ? <a className="mono" href={`#/cases/${r.case_id}`}>{short(r.case_id, 14)}</a> : "—" },
              { key: "k", label: "Correlation", render: (r) => r.correlation_id ? <a className="mono" href={`#/audit?correlation_id=${r.correlation_id}`}>{short(r.correlation_id, 14)}</a> : "—" },
              { key: "d", label: "Details", render: (r) => <span className="mono muted" style={{ fontSize: 12 }}>{JSON.stringify(r.data).slice(0, 180)}</span> },
            ]} />
          {cursor && <div style={{ marginTop: 12 }}><Button busy={more} onClick={async () => { setMore(true);
            try { const r = await api.audit({ ...params, limit: 50, cursor }); setItems((x) => [...x, ...r.items]); setCursor(r.next_cursor); } finally { setMore(false); } }}>Load older</Button></div>}
        </>)}
      <div style={{ marginTop: 18 }}>
        <Card title="Unmatched provider events" sub="Verified webhooks that referenced no known attempt. Recorded, never applied." id="unm">
          <Resource res={unmatched}>{(u) => (
            <Table caption="Unmatched provider events" rows={u.items} rowKey={(e) => e.provider_event_id} empty="None"
              columns={[{ key: "id", label: "Event id", render: (e) => <span className="mono">{e.provider_event_id}</span> },
                { key: "t", label: "Type", render: (e) => e.event_type },
                { key: "r", label: "Provider reference", render: (e) => <span className="mono">{e.summary?.provider_reference || "—"}</span> },
                { key: "a", label: "Amount", num: true, render: (e) => money(e.summary?.amount_paise) },
                { key: "w", label: "Received", render: (e) => when(e.received_at) }]} />)}
          </Resource>
        </Card>
      </div>
    </Page>
  );
}

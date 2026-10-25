import { useEffect, useState } from "react";
import { api } from "../lib/api.js";
import { money, short, when } from "../lib/format.js";
import { go, useResource } from "../lib/hooks.js";
import { Button, ErrorBox, Loading, Page, Status, Table, Tag } from "../components/ui.jsx";

const STATUSES = ["", "OPEN", "PENDING_APPROVAL", "ACTION_QUEUED", "PROVIDER_PENDING", "OUTCOME_UNKNOWN", "NEEDS_REVIEW",
  "RECOVERED", "EXPIRED", "STILL_FAILED", "ACTION_REJECTED", "ACTION_FAILED", "NO_ACTION"];

export default function Cases({ query }) {
  const [status, setStatus] = useState(query.status || "");
  const [q, setQ] = useState(query.q || "");
  const [items, setItems] = useState([]);
  const [cursor, setCursor] = useState(null);
  const [more, setMore] = useState(false);
  const page = useResource(() => api.cases({ status: query.status, q: query.q, limit: 25 }), [query.status, query.q]);
  useEffect(() => { if (page.data) { setItems(page.data.items); setCursor(page.data.next_cursor); } }, [page.data]);
  useEffect(() => { setStatus(query.status || ""); setQ(query.q || ""); }, [query.status, query.q]);
  const loadMore = async () => {
    setMore(true);
    try { const r = await api.cases({ status: query.status, q: query.q, limit: 25, cursor }); setItems((x) => [...x, ...r.items]); setCursor(r.next_cursor); }
    finally { setMore(false); }
  };
  const multi = (query.status || "").includes(",");
  return (
    <Page eyebrow="Ledger" title="Recovery cases" intro="Pseudonymous synthetic cases. Select a row for the decision, provider attempt, reconciliation and full audit timeline.">
      <form className="filters" onSubmit={(e) => { e.preventDefault(); go("/cases", { status, q: q.trim() }); }}>
        <label className="field"><span>Status</span>
          <select value={multi ? "" : status} onChange={(e) => go("/cases", { status: e.target.value, q })}>
            {multi && <option value="">(open exposure statuses)</option>}
            {STATUSES.map((s) => <option key={s} value={s}>{s ? s.replaceAll("_", " ") : "All statuses"}</option>)}</select></label>
        <label className="field"><span>Exact id (case, payment, customer, provider ref)</span>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="e.g. DEMO-001" /></label>
        <Button type="submit">Search</Button>
        {(query.status || query.q) && <button type="button" className="btn" onClick={() => go("/cases")}>Clear</button>}
      </form>
      {page.loading && !items.length ? <Loading label="Loading cases" /> : page.error ? <ErrorBox error={page.error} onRetry={page.reload} /> : (
        <>
          <Table caption="Recovery cases" rows={items} rowKey={(r) => r.case_id} onRowClick={(r) => go("/cases/" + r.case_id)}
                 empty="No cases match these filters"
                 columns={[
                   { key: "external_payment_id", label: "Payment", render: (r) => <><div>{r.external_payment_id}</div><div className="muted mono">{short(r.case_id, 14)}</div></> },
                   { key: "amount_paise", label: "Amount", num: true, render: (r) => money(r.amount_paise) },
                   { key: "failure_reason", label: "Failure", render: (r) => <>{r.failure_reason.replaceAll("_", " ")}<div className="muted">{r.payment_method}</div></> },
                   { key: "selected_action", label: "Decision", render: (r) => r.selected_action ? r.selected_action.replaceAll("_", " ").toLowerCase() : "—" },
                   { key: "expected_value_paise", label: "Exp. incremental", num: true, render: (r) => r.selected_action && r.selected_action !== "NO_ACTION" ? money(r.expected_value_paise) : "—" },
                   { key: "status", label: "Status", render: (r) => <><Status value={r.status} />{r.review_reason && <div className="muted">flag: {r.review_reason}</div>}</> },
                   { key: "mode", label: "Provenance", render: (r) => <><Tag kind="SYNTHETIC" />{r.provider_mode && <> <Tag kind={r.provider_mode} /></>}</> },
                   { key: "created_at", label: "Received", render: (r) => <span className="muted">{when(r.created_at)}</span> },
                 ]} />
          {cursor && <div style={{ marginTop: 12 }}><Button busy={more} onClick={loadMore}>Load more</Button></div>}
        </>)}
    </Page>
  );
}

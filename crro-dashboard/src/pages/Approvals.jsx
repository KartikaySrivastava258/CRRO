import { useState } from "react";
import { api } from "../lib/api.js";
import { ago, human, money, pct, when } from "../lib/format.js";
import { useResource } from "../lib/hooks.js";
import { ConfirmDialog, Page, Resource, Status, Table, Tag } from "../components/ui.jsx";

const TABS = ["PENDING", "APPROVED", "REJECTED", "EXPIRED", "ALL"];

export default function Approvals({ me }) {
  const [tab, setTab] = useState("PENDING");
  const res = useResource(() => api.approvals({ status: tab, limit: 100 }), [tab]);
  const [dlg, setDlg] = useState(null);       // { approval, decision }
  const [result, setResult] = useState(null);
  const canOperate = me.role === "operator";
  const confirm = async (note) => {
    const r = await api.decide(dlg.approval.approval_id, dlg.decision, note);   // throws -> dialog shows error
    setResult({ ok: true, text: `${dlg.decision === "APPROVE" ? "Approved" : "Rejected"} ${dlg.approval.approval_id}` +
      (r.attempt ? ` — provider attempt ${r.attempt.status.toLowerCase().replaceAll("_", " ")}` : "") + (r.replayed ? " (already decided; no change)" : "") });
    setDlg(null); res.reload();
  };
  return (
    <Page eyebrow="Controls" title="Approval queue"
          intro="High-value actions wait here. Decisions are compare-and-set on the server: a repeated identical decision is a no-op, a conflicting one is rejected, and an expired request cannot be approved.">
      <div className="tabs" role="tablist" aria-label="Approval status">
        {TABS.map((t) => <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}>{human(t)}</button>)}
      </div>
      {!canOperate && <div className="callout warn" style={{ marginBottom: 12 }}>Read-only role: approve/reject are disabled and also rejected by the server (403).</div>}
      {result && <div className="callout" role="status" style={{ marginBottom: 12 }}>{result.text}</div>}
      <Resource res={res} label="Loading approvals">{(d) => (
        <Table caption="Approval requests" rows={d.items} rowKey={(a) => a.approval_id} empty={`No ${tab.toLowerCase()} approvals`}
          columns={[
            { key: "case", label: "Case", render: (a) => <><a href={`#/cases/${a.case_id}`} className="mono">{a.case_id}</a><div className="muted">{human(a.failure_reason)} · {a.payment_method}</div></> },
            { key: "amt", label: "Amount", num: true, render: (a) => money(a.amount_paise) },
            { key: "act", label: "Proposed action", render: (a) => human(a.action) },
            { key: "why", label: "Why approval", render: (a) => <><div>{a.reason}</div><div className="muted">threshold {money(a.threshold_paise)}</div></> },
            { key: "ev", label: "Expected incremental", num: true, render: (a) => <>{money(a.expected_value_paise)}<div><Tag kind="ESTIMATE" /></div></> },
            { key: "p", label: "P(no action → action)", num: true, render: (a) => `${pct(a.p_control)} → ${pct(a.p_selected)}` },
            { key: "st", label: "Status", render: (a) => <><Status value={a.status} />{a.decided_by && <div className="muted">{a.decided_by}</div>}</> },
            { key: "t", label: "Requested", render: (a) => <><div>{ago(a.created_at)}</div><div className="muted">expires {when(a.expires_at)}</div></> },
            { key: "do", label: "Decision", render: (a) => a.status === "PENDING" ? (
              <div style={{ display: "flex", gap: 6 }}>
                <button className="btn btn-sm btn-primary" disabled={!canOperate} onClick={() => setDlg({ approval: a, decision: "APPROVE" })}>Approve</button>
                <button className="btn btn-sm btn-danger" disabled={!canOperate} onClick={() => setDlg({ approval: a, decision: "REJECT" })}>Reject</button>
              </div>) : <span className="muted">{a.decision_note || "—"}</span> },
          ]} />)}
      </Resource>
      <ConfirmDialog open={!!dlg} title={dlg ? `${dlg.decision === "APPROVE" ? "Approve" : "Reject"} ${human(dlg.approval.action)} for ${money(dlg.approval.amount_paise)}?` : ""}
        confirmLabel={dlg?.decision === "APPROVE" ? "Approve and dispatch" : "Reject"} tone={dlg?.decision === "REJECT" ? "danger" : "primary"}
        requireNote={dlg?.decision === "REJECT"} onCancel={() => setDlg(null)} onConfirm={confirm}>
        {dlg?.decision === "APPROVE"
          ? "Approval reserves budget and dispatches the action to the provider immediately. It is recorded with your identity."
          : "Rejection closes the case without contacting the customer. A reason is required."}
      </ConfirmDialog>
    </Page>
  );
}

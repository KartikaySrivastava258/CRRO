import { useState } from "react";
import { Check, X } from "lucide-react";
import { endpoints } from "../lib/api";
import { useApi } from "../hooks/useApi";
import { PageHeader, Panel, Loading, ErrorState, EmptyState, formatInr, formatPct } from "../components/Ui";

export default function Approvals() {
  const { data, loading, error, reload } = useApi(() => endpoints.pendingApprovals(50), []);
  const [busy, setBusy] = useState(null);
  const [toast, setToast] = useState(null);

  async function act(decisionId, kind) {
    setBusy(decisionId);
    try {
      if (kind === "approve") await endpoints.approve(decisionId);
      else await endpoints.reject(decisionId);
      setToast({ kind, decisionId });
      reload();
    } catch (e) {
      setToast({ kind: "error", message: e?.response?.data?.detail || e.message });
    } finally {
      setBusy(null);
    }
  }

  return (
    <div>
      <PageHeader
        eyebrow="Human Approval Workflow"
        title="Pending approvals"
        subtitle="Approving or rejecting here calls the real backend endpoint and persists the state -- it is not simulated in the frontend."
      />
      <div className="space-y-4 p-8">
        {toast && (
          <div
            className="rounded-md px-4 py-2.5 text-[13px]"
            style={{
              background: toast.kind === "error" ? "var(--blocked-soft)" : "var(--recovered-soft)",
              color: toast.kind === "error" ? "var(--blocked)" : "var(--recovered)",
            }}
          >
            {toast.kind === "approve" && `Decision ${toast.decisionId} approved and sent for execution.`}
            {toast.kind === "reject" && `Decision ${toast.decisionId} rejected.`}
            {toast.kind === "error" && `Error: ${toast.message}`}
          </div>
        )}
        {loading && <Loading label="Loading pending approvals" />}
        {error && <ErrorState error={error} onRetry={reload} />}
        {data && data.pending.length === 0 && (
          <EmptyState label="Nothing waiting on human approval right now." />
        )}
        {data && data.pending.map((p) => (
          <Panel key={p.decision_id}>
            <div className="flex items-start justify-between gap-6">
              <div className="flex-1">
                <div className="flex items-center gap-3">
                  <span className="font-mono-tab text-[13px] font-medium">{p.case_id}</span>
                  <span className="text-[12px]" style={{ color: "var(--muted)" }}>{p.customer_id}</span>
                </div>
                <div className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 text-[13px] sm:grid-cols-4">
                  <Field label="Failed payment" value={formatInr(p.amount_inr)} />
                  <Field label="Failure reason" value={p.failure_reason} />
                  <Field label="AI recommendation" value={p.recommended_action} />
                  <Field label="Expected recovery" value={formatPct(p.recovery_probability)} />
                  <Field label="Expected value" value={formatInr(p.expected_incremental_value_inr)} />
                  <Field label="Action cost" value={formatInr(p.action_cost_inr)} />
                </div>
                <p className="mt-3 rounded-md px-3 py-2 text-[12px]" style={{ background: "var(--pending-soft)", color: "var(--pending)" }}>
                  {p.why_approval_required}
                </p>
                {p.llm_evidence?.length > 0 && (
                  <ul className="mt-3 space-y-1 text-[12px]" style={{ color: "var(--ink-soft)" }}>
                    {p.llm_evidence.map((b, i) => <li key={i}>&bull; {b}</li>)}
                  </ul>
                )}
              </div>
              <div className="flex shrink-0 flex-col gap-2">
                <button
                  disabled={busy === p.decision_id}
                  onClick={() => act(p.decision_id, "approve")}
                  className="flex items-center gap-1.5 rounded-md px-3.5 py-2 text-[12.5px] font-medium text-white disabled:opacity-50"
                  style={{ background: "var(--recovered)" }}
                >
                  <Check size={14} /> Approve
                </button>
                <button
                  disabled={busy === p.decision_id}
                  onClick={() => act(p.decision_id, "reject")}
                  className="flex items-center gap-1.5 rounded-md border px-3.5 py-2 text-[12.5px] font-medium disabled:opacity-50"
                  style={{ borderColor: "var(--blocked)", color: "var(--blocked)" }}
                >
                  <X size={14} /> Reject
                </button>
              </div>
            </div>
          </Panel>
        ))}
      </div>
    </div>
  );
}

function Field({ label, value }) {
  return (
    <div>
      <dt className="text-[11px]" style={{ color: "var(--muted)" }}>{label}</dt>
      <dd className="font-mono-tab" style={{ color: "var(--ink)" }}>{value}</dd>
    </div>
  );
}

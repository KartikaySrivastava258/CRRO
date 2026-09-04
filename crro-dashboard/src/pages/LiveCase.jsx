import { useEffect, useMemo, useState } from "react";
import { Search, CircleDot, CheckCircle2, XCircle, Clock, Play } from "lucide-react";
import { endpoints } from "../lib/api";
import { useApi } from "../hooks/useApi";
import { PageHeader, Panel, Loading, ErrorState, EmptyState, StatusBadge, formatInr, formatPct } from "../components/Ui";

export default function LiveCase() {
  const [query, setQuery] = useState("");
  const [caseId, setCaseId] = useState(null);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState(null);
  const { data: caseList, reload: reloadCases } = useApi(() => endpoints.cases(200, 0), []);

  const filtered = useMemo(() => {
    const rows = (caseList?.cases || []).filter((c) => c.source === "live");
    if (!query) return rows.slice(0, 25);
    return rows.filter((c) => c.case_id.toLowerCase().includes(query.toLowerCase())).slice(0, 25);
  }, [caseList, query]);

  useEffect(() => {
    if (!caseId && filtered.length) setCaseId(filtered[0].case_id);
  }, [filtered, caseId]);

  const startLiveCase = async () => {
    setStarting(true);
    setStartError(null);
    const id = `DEMO-UI-${Date.now()}`;
    try {
      const res = await endpoints.submitFailedPayment({
        case_id: id,
        merchant_id: "merchant_demo",
        customer_id: `cust_demo_${Date.now()}`,
        amount_inr: 15000,
        payment_method: "card",
        failure_reason: "card_expired",
        attempt_count: 1,
        minutes_since_failure: 10,
      });
      await reloadCases();
      setCaseId(res.data.case_id || id);
    } catch (err) {
      setStartError(err?.response?.data?.detail || err?.message || "Could not start the live recovery case.");
    } finally {
      setStarting(false);
    }
  };

  return (
    <div>
      <PageHeader
        eyebrow="Live Recovery Case"
        title="Follow one failed payment end to end"
        subtitle="Failure -> evidence -> AI diagnosis -> recommended action -> guardrails -> optimizer -> approval -> execution -> outcome."
        right={
          <button
            onClick={startLiveCase}
            disabled={starting}
            className="inline-flex items-center gap-2 rounded-md px-3 py-2 text-[12px] font-medium disabled:opacity-60"
            style={{ background: "var(--action)", color: "white" }}
          >
            <Play size={13} />
            {starting ? "Starting…" : "Start live demo case"}
          </button>
        }
      />
      <div className="grid grid-cols-[280px_1fr] gap-0" style={{ minHeight: "calc(100vh - 97px)" }}>
        <aside className="border-r px-4 py-5" style={{ borderColor: "var(--line)" }}>
          <div className="relative mb-3">
            <Search size={13} className="absolute left-2.5 top-2.5" style={{ color: "var(--muted)" }} />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search case ID"
              className="w-full rounded-md border py-1.5 pl-7 pr-2 text-[12px] outline-none"
              style={{ borderColor: "var(--line)" }}
            />
          </div>
          <div className="flex flex-col gap-0.5 overflow-y-auto" style={{ maxHeight: "calc(100vh - 180px)" }}>
            {filtered.map((c) => (
              <button
                key={c.case_id}
                onClick={() => setCaseId(c.case_id)}
                className="rounded-md px-2.5 py-2 text-left text-[12px] transition-colors"
                style={{
                  background: c.case_id === caseId ? "var(--action-soft)" : "transparent",
                  color: c.case_id === caseId ? "var(--action)" : "var(--ink-soft)",
                }}
              >
                <div className="font-mono-tab font-medium">{c.case_id}</div>
                <div className="text-[11px]" style={{ color: "var(--muted)" }}>
                  {c.failure_reason} &middot; {formatInr(c.amount_inr)}
                </div>
              </button>
            ))}
            {filtered.length === 0 && (
              <div className="px-2 py-6 text-[12px]" style={{ color: "var(--muted)" }}>
                No live cases yet. Click “Start live demo case”.
              </div>
            )}
          </div>
        </aside>
        <div className="p-8">
          {startError && (
            <div className="mb-4 rounded-md border px-3 py-2 text-[12px]" style={{ borderColor: "var(--blocked)", color: "var(--blocked)", background: "var(--blocked-soft)" }}>
              {startError}
            </div>
          )}
          {caseId ? <CaseJourney caseId={caseId} /> : <EmptyState label="Start a live recovery case to see the end-to-end journey." />}
        </div>
      </div>
    </div>
  );
}

const STAGES = [
  { key: "failed", label: "Payment Failed" },
  { key: "evidence", label: "Customer / Payment Evidence" },
  { key: "diagnosis", label: "AI Diagnosis" },
  { key: "recommendation", label: "Recommended Intervention" },
  { key: "guardrails", label: "Eligibility & Guardrails" },
  { key: "optimizer", label: "Optimizer Decision" },
  { key: "approval", label: "Approval" },
  { key: "execution", label: "Action Executed" },
  { key: "outcome", label: "Payment Recovered / Outcome" },
];

function CaseJourney({ caseId }) {
  const { data, loading, error, reload } = useApi(() => endpoints.caseJourney(caseId), [caseId]);
  const { data: explain } = useApi(() => endpoints.caseExplainability(caseId), [caseId]);

  if (loading) return <Loading label="Loading case journey" />;
  if (error) return <ErrorState error={error} onRetry={reload} />;
  if (!data) return null;

  const { case: c, decisions, audit_events } = data;
  const latest = decisions[decisions.length - 1];

  const stageStatus = (key) => {
    if (key === "failed") return "done";
    if (key === "evidence" || key === "diagnosis") return latest ? "done" : "pending";
    if (key === "recommendation") return latest ? "done" : "pending";
    if (key === "guardrails") return latest ? "done" : "pending";
    if (key === "optimizer") return latest ? "done" : "pending";
    if (key === "approval") {
      if (!latest?.approved && latest) return "blocked";
      if (latest?.execution_status === "PENDING_APPROVAL") return "active";
      return latest ? "done" : "pending";
    }
    if (key === "execution") {
      if (latest?.execution_status === "EXECUTED" || latest?.execution_status === "RECOVERED") return "done";
      if (latest?.execution_status === "PENDING_APPROVAL") return "pending";
      if (latest?.execution_status === "NOT_EXECUTED") return "skipped";
      return "pending";
    }
    if (key === "outcome") return c.status === "RECOVERED" ? "done" : c.status === "CLOSED_UNRECOVERED" ? "blocked" : "pending";
    return "pending";
  };

  return (
    <div className="grid grid-cols-[280px_1fr] gap-8">
      <ol className="space-y-0.5">
        {STAGES.map((s) => (
          <StageRow key={s.key} label={s.label} status={stageStatus(s.key)} />
        ))}
      </ol>

      <div className="space-y-5">
        <Panel title={`Case ${c.case_id}`} action={<StatusBadge status={c.status} />}>
          <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-[13px] sm:grid-cols-3">
            <Field label="Amount" value={formatInr(c.amount_inr)} />
            <Field label="Recovered amount" value={formatInr(c.recovered_amount_inr)} />
            <Field label="Recovered at" value={c.recovered_at ? new Date(c.recovered_at).toLocaleString() : "\u2014"} />
          </dl>
        </Panel>

        {latest && (
          <Panel title="AI diagnosis & recommended action">
            <p className="text-[13px]" style={{ color: "var(--ink-soft)" }}>{latest.llm_diagnosis || "\u2014"}</p>
            <div className="mt-3 flex flex-wrap gap-2">
              {(latest.llm_evidence || []).map((b, i) => (
                <span key={i} className="rounded px-2 py-1 text-[11px]" style={{ background: "var(--action-soft)", color: "var(--action)" }}>
                  {b}
                </span>
              ))}
            </div>
            <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-2 text-[13px] sm:grid-cols-4">
              <Field label="Recommended action" value={latest.action} />
              <Field label="AI confidence" value={formatPct(latest.llm_confidence)} />
              <Field label="Requires approval" value={latest.approved === false ? "Blocked" : (data.decisions.length && latest.reason_code === "APPROVAL_PENDING" ? "Yes" : "No")} />
              <Field label="Execution status" value={<StatusBadge status={latest.execution_status} />} />
              <Field label="Provider reference" value={latest.provider_reference || "\u2014"} />
              <Field label="Guardrail reason" value={latest.reason_code || "\u2014"} />
            </dl>
          </Panel>
        )}

        {explain && explain.rejected_alternatives?.length > 0 && (
          <Panel title="Alternative actions considered">
            <ul className="space-y-1.5 text-[13px]">
              {explain.rejected_alternatives.map((r) => (
                <li key={r.action} className="flex items-center justify-between">
                  <span>{r.action}</span>
                  <span className="font-mono-tab" style={{ color: "var(--muted)" }}>{formatInr(r.expected_incremental_value_inr)}</span>
                </li>
              ))}
            </ul>
          </Panel>
        )}

        <Panel title="Audit trail for this case">
          {audit_events.length === 0 ? (
            <EmptyState label="No audit events yet." />
          ) : (
            <ul className="space-y-3">
              {audit_events.map((e, i) => (
                <li key={i} className="flex gap-3 text-[12px]">
                  <Clock size={13} className="mt-0.5 shrink-0" style={{ color: "var(--muted)" }} />
                  <div>
                    <div className="font-medium" style={{ color: "var(--ink)" }}>{e.event_type.replaceAll("_", " ")}</div>
                    <div style={{ color: "var(--muted)" }}>{new Date(e.ts).toLocaleString()}</div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>
    </div>
  );
}

function StageRow({ label, status }) {
  const icon = {
    done: <CheckCircle2 size={15} style={{ color: "var(--recovered)" }} />,
    active: <CircleDot size={15} style={{ color: "var(--pending)" }} />,
    blocked: <XCircle size={15} style={{ color: "var(--blocked)" }} />,
    skipped: <CircleDot size={15} style={{ color: "var(--muted)" }} />,
    pending: <CircleDot size={15} style={{ color: "var(--line-strong)" }} />,
  }[status];
  return (
    <li className="flex items-center gap-2.5 py-2 pl-3" style={{ borderLeft: `2px solid ${status === "pending" ? "var(--line)" : "var(--line-strong)"}` }}>
      {icon}
      <span className="text-[12.5px]" style={{ color: status === "pending" ? "var(--muted)" : "var(--ink)" }}>{label}</span>
    </li>
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

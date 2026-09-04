import { useEffect, useState } from "react";
import { CheckCircle2 } from "lucide-react";
import { endpoints } from "../lib/api";
import { useApi } from "../hooks/useApi";
import { PageHeader, Panel, Loading, ErrorState, EmptyState, StatusBadge, formatInr, formatPct } from "../components/Ui";

export default function Explainability() {
  const [caseId, setCaseId] = useState("");
  const [submitted, setSubmitted] = useState(null);
  const { data: caseList } = useApi(() => endpoints.cases(1, 0), []);

  useEffect(() => {
    if (!submitted && caseList?.cases?.length) {
    }
  }, [caseList, submitted]);

  return (
    <div>
      <PageHeader
        eyebrow="Explainability"
        title="Why did CRRO recommend this?"
        subtitle="Assembled strictly from the decision actually persisted for this case -- not a fresh re-prompt."
        right={
          <form
            className="flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              setSubmitted(caseId.trim());
            }}
          >
            <input
              value={caseId}
              onChange={(e) => setCaseId(e.target.value)}
              placeholder="Case ID, e.g. DEMO-0001"
              className="w-56 rounded-md border px-3 py-1.5 text-[12px] outline-none"
              style={{ borderColor: "var(--line)" }}
            />
            <button
              type="submit"
              className="rounded-md px-3 py-1.5 text-[12px] font-medium text-white"
              style={{ background: "var(--action)" }}
            >
              Explain
            </button>
          </form>
        }
      />
      <div className="p-8">
        {!submitted && <EmptyState label="Enter a case ID to see its explanation." />}
        {submitted && <ExplainBody caseId={submitted} />}
      </div>
    </div>
  );
}

function ExplainBody({ caseId }) {
  const { data, loading, error, reload } = useApi(() => endpoints.caseExplainability(caseId), [caseId]);

  if (loading) return <Loading label="Assembling explanation" />;
  if (error) return <ErrorState error={error} onRetry={reload} />;
  if (!data) return null;

  return (
    <div className="grid max-w-4xl grid-cols-1 gap-5">
      <Panel
        title={`Why "${data.selected_action}" was selected`}
        action={<StatusBadge status={data.execution_status} />}
      >
        <ul className="space-y-2">
          {data.why_this_action.map((reason, i) => (
            <li key={i} className="flex items-start gap-2 text-[13px]" style={{ color: "var(--ink-soft)" }}>
              <CheckCircle2 size={14} className="mt-0.5 shrink-0" style={{ color: "var(--recovered)" }} />
              {reason}
            </li>
          ))}
        </ul>
        <div
          className="mt-4 rounded-md px-3 py-2.5 font-mono-tab text-[12px]"
          style={{ background: "var(--action-soft)", color: "var(--action)" }}
        >
          &rarr; {data.selected_action} selected
        </div>
      </Panel>

      <Panel title="AI diagnosis">
        <p className="text-[13px]" style={{ color: "var(--ink-soft)" }}>{data.diagnosis}</p>
        <div className="mt-3 grid grid-cols-3 gap-4 text-[13px]">
          <Field label="AI confidence" value={formatPct(data.ai_confidence)} />
          <Field label="Budget impact" value={formatInr(data.budget_impact_inr)} />
          <Field label="Human approval" value={data.guardrails_applied.requires_human_approval ? "Required" : "Not required"} />
        </div>
      </Panel>

      <Panel title="Evidence retrieved from PostgreSQL">
        <ul className="space-y-1.5 text-[13px]" style={{ color: "var(--ink-soft)" }}>
          {(Array.isArray(data.evidence) ? data.evidence : [data.evidence]).map((b, i) => (
            <li key={i}>&bull; {b}</li>
          ))}
        </ul>
      </Panel>

      <Panel title="Guardrails applied">
        <dl className="grid grid-cols-3 gap-4 text-[13px]">
          <Field label="Policy approved" value={data.guardrails_applied.approved ? "Yes" : "No"} />
          <Field label="Reason code" value={data.guardrails_applied.reason_code || "\u2014"} />
          <Field label="Provider reference" value={data.provider_reference || "\u2014"} />
        </dl>
      </Panel>

      {data.rejected_alternatives?.length > 0 && (
        <Panel title="Why other actions were rejected">
          <table className="w-full text-[13px]">
            <thead>
              <tr className="text-left" style={{ color: "var(--muted)" }}>
                <th className="pb-1.5 font-normal">Action</th>
                <th className="pb-1.5 font-normal">Expected value</th>
                <th className="pb-1.5 font-normal">Reason</th>
              </tr>
            </thead>
            <tbody>
              {data.rejected_alternatives.map((r) => (
                <tr key={r.action}>
                  <td className="py-1 pr-3 font-medium">{r.action}</td>
                  <td className="py-1 pr-3 font-mono-tab">{formatInr(r.expected_incremental_value_inr)}</td>
                  <td className="py-1" style={{ color: "var(--muted)" }}>{r.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      )}
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

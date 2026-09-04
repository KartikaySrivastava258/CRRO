import { useState } from "react";
import { Search } from "lucide-react";
import { endpoints } from "../lib/api";
import { useApi } from "../hooks/useApi";
import { PageHeader, Panel, Loading, ErrorState, EmptyState, StatusBadge, formatInr } from "../components/Ui";

const EVENT_TYPES = [
  "", "DECISION_RECORDED", "API_DECISION_RECORDED", "ACTION_EXECUTED",
  "WEBHOOK_RECONCILED", "APPROVAL_GRANTED", "APPROVAL_REJECTED",
];
const STATUSES = ["", "OPEN", "RECOVERED", "CLOSED_UNRECOVERED"];

export default function Audit() {
  const [filters, setFilters] = useState({ case_id: "", event_type: "", status: "", q: "" });
  const { data, loading, error, reload } = useApi(
    () => endpoints.audit({ ...cleanFilters(filters), limit: 200 }),
    [filters.case_id, filters.event_type, filters.status, filters.q]
  );

  return (
    <div>
      <PageHeader
        eyebrow="Audit / Activity"
        title="Chronological system activity"
        subtitle="Every recovery decision, execution, approval, and reconciliation, searchable and filterable."
      />
      <div className="space-y-5 p-8">
        <Panel>
          <div className="flex flex-wrap items-center gap-3">
            <div className="relative">
              <Search size={13} className="absolute left-2.5 top-2.5" style={{ color: "var(--muted)" }} />
              <input
                value={filters.q}
                onChange={(e) => setFilters((f) => ({ ...f, q: e.target.value }))}
                placeholder="Search event, case ID, payload..."
                className="w-64 rounded-md border py-1.5 pl-7 pr-2 text-[12px] outline-none"
                style={{ borderColor: "var(--line)" }}
              />
            </div>
            <input
              value={filters.case_id}
              onChange={(e) => setFilters((f) => ({ ...f, case_id: e.target.value }))}
              placeholder="Case ID"
              className="w-36 rounded-md border px-2.5 py-1.5 text-[12px] outline-none"
              style={{ borderColor: "var(--line)" }}
            />
            <Select value={filters.event_type} onChange={(v) => setFilters((f) => ({ ...f, event_type: v }))} options={EVENT_TYPES} placeholder="Event type" />
            <Select value={filters.status} onChange={(v) => setFilters((f) => ({ ...f, status: v }))} options={STATUSES} placeholder="Case status" />
          </div>
        </Panel>

        <Panel>
          {loading && <Loading label="Loading audit trail" />}
          {error && <ErrorState error={error} onRetry={reload} />}
          {data && data.length === 0 && <EmptyState label="No matching audit events." />}
          {data && data.length > 0 && (
            <table className="w-full text-[12.5px]">
              <thead>
                <tr className="text-left" style={{ color: "var(--muted)" }}>
                  <th className="pb-2 font-normal">Timestamp</th>
                  <th className="pb-2 font-normal">Case</th>
                  <th className="pb-2 font-normal">Amount</th>
                  <th className="pb-2 font-normal">Event type</th>
                  <th className="pb-2 font-normal">Case status</th>
                  <th className="pb-2 font-normal">Detail</th>
                </tr>
              </thead>
              <tbody>
                {data.map((e) => (
                  <tr key={e.event_id} className="border-t" style={{ borderColor: "var(--line)" }}>
                    <td className="py-2 pr-3 font-mono-tab whitespace-nowrap" style={{ color: "var(--muted)" }}>
                      {new Date(e.ts).toLocaleString()}
                    </td>
                    <td className="py-2 pr-3 font-mono-tab">{e.case_id || "\u2014"}</td>
                    <td className="py-2 pr-3 font-mono-tab">{formatInr(e.amount_inr)}</td>
                    <td className="py-2 pr-3 font-medium">{e.event_type.replaceAll("_", " ")}</td>
                    <td className="py-2 pr-3"><StatusBadge status={e.case_status} /></td>
                    <td className="py-2 pr-3 max-w-md truncate" style={{ color: "var(--ink-soft)" }} title={JSON.stringify(e.payload)}>
                      {summarizePayload(e.payload)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Panel>
      </div>
    </div>
  );
}

function cleanFilters(f) {
  const out = {};
  Object.entries(f).forEach(([k, v]) => { if (v) out[k] = v; });
  return out;
}

function summarizePayload(payload) {
  if (!payload) return "\u2014";
  try {
    return Object.entries(payload).map(([k, v]) => `${k}: ${v}`).join(", ");
  } catch {
    return String(payload);
  }
}

function Select({ value, onChange, options, placeholder }) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="rounded-md border px-2.5 py-1.5 text-[12px] outline-none"
      style={{ borderColor: "var(--line)", color: value ? "var(--ink)" : "var(--muted)" }}
    >
      <option value="">{placeholder}</option>
      {options.filter(Boolean).map((o) => (
        <option key={o} value={o}>{o.replaceAll("_", " ")}</option>
      ))}
    </select>
  );
}

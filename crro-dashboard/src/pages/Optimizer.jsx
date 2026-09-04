import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { endpoints } from "../lib/api";
import { useApi } from "../hooks/useApi";
import { PageHeader, Panel, Loading, ErrorState, EmptyState, StatusBadge, formatInr, formatPct } from "../components/Ui";

export default function Optimizer() {
  const { data, loading, error, reload } = useApi(() => endpoints.optimizer(20), []);
  const [openCase, setOpenCase] = useState(null);

  return (
    <div>
      <PageHeader
        eyebrow="Optimizer"
        title="How CRRO decides which actions to execute"
        subtitle="Every candidate the deterministic uplift model + policy gate evaluated -- CRRO does not simply ask an LLM what to do."
      />
      <div className="space-y-6 p-8">
        {loading && <Loading label="Loading optimizer decisions" />}
        {error && <ErrorState error={error} onRetry={reload} />}
        {data && (
          <>
            <Panel title="Portfolio budget">
              <div className="flex items-center gap-8 text-[13px]">
                <div>
                  <div className="text-[11px]" style={{ color: "var(--muted)" }}>Contacts used today</div>
                  <div className="font-mono-tab text-[18px]">
                    {data.portfolio_budget.contacts_used_today} / {data.portfolio_budget.daily_contact_budget}
                  </div>
                </div>
                <div className="h-2 flex-1 overflow-hidden rounded-full" style={{ background: "var(--line)" }}>
                  <div
                    className="h-full rounded-full"
                    style={{
                      width: `${Math.min(100, (data.portfolio_budget.contacts_used_today / Math.max(1, data.portfolio_budget.daily_contact_budget)) * 100)}%`,
                      background: "var(--action)",
                    }}
                  />
                </div>
              </div>
            </Panel>

            <Panel title="Recent decisions">
              {(!data.recent_decisions || data.recent_decisions.length === 0) ? (
                <EmptyState label="No decisions recorded yet -- trigger one via demo.sh or /events/payment-failed." />
              ) : (
                <div className="divide-y" style={{ borderColor: "var(--line)" }}>
                  {data.recent_decisions.map((d) => (
                    <div key={d.case_id} className="py-3">
                      <button
                        className="flex w-full items-center justify-between text-left"
                        onClick={() => setOpenCase(openCase === d.case_id ? null : d.case_id)}
                      >
                        <div className="flex items-center gap-3">
                          {openCase === d.case_id ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                          <span className="font-mono-tab text-[13px]">{d.case_id}</span>
                          <span className="text-[12px]" style={{ color: "var(--muted)" }}>{formatInr(d.amount_inr)}</span>
                        </div>
                        <div className="flex items-center gap-3">
                          <span className="text-[12px] font-medium" style={{ color: "var(--action)" }}>{d.selected_action}</span>
                          <StatusBadge status={d.execution_status} />
                        </div>
                      </button>
                      {openCase === d.case_id && (
                        <div className="mt-3 pl-6">
                          {d.candidate_note && (
                            <p className="mb-2 text-[11px]" style={{ color: "var(--muted)" }}>{d.candidate_note}</p>
                          )}
                          {d.candidate_actions.length > 0 && (
                            <table className="w-full text-[12px]">
                              <thead>
                                <tr className="text-left" style={{ color: "var(--muted)" }}>
                                  <th className="pb-1.5 font-normal">Action</th>
                                  <th className="pb-1.5 font-normal">Recovery prob.</th>
                                  <th className="pb-1.5 font-normal">Uplift</th>
                                  <th className="pb-1.5 font-normal">Expected value</th>
                                  <th className="pb-1.5 font-normal">Cost</th>
                                  <th className="pb-1.5 font-normal">Contact?</th>
                                  <th className="pb-1.5 font-normal">Outcome</th>
                                </tr>
                              </thead>
                              <tbody>
                                {d.candidate_actions.map((c) => (
                                  <tr
                                    key={c.action}
                                    style={{
                                      background: c.selected ? "var(--recovered-soft)" : "transparent",
                                    }}
                                  >
                                    <td className="py-1.5 pr-2 font-medium">{c.action}</td>
                                    <td className="py-1.5 pr-2 font-mono-tab">{formatPct(c.recovery_probability)}</td>
                                    <td className="py-1.5 pr-2 font-mono-tab">{formatPct(c.expected_uplift)}</td>
                                    <td className="py-1.5 pr-2 font-mono-tab">{formatInr(c.expected_incremental_value_inr)}</td>
                                    <td className="py-1.5 pr-2 font-mono-tab">{formatInr(c.action_cost_inr)}</td>
                                    <td className="py-1.5 pr-2">{c.requires_customer_contact ? "Yes" : "No"}</td>
                                    <td className="py-1.5" style={{ color: c.selected ? "var(--recovered)" : "var(--muted)" }}>{c.reason}</td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          )}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </Panel>
          </>
        )}
      </div>
    </div>
  );
}

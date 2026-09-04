import {
  LineChart, Line, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, PieChart, Pie, Cell,
} from "recharts";
import { endpoints } from "../lib/api";
import { useApi } from "../hooks/useApi";
import { PageHeader, Panel, Kpi, Loading, ErrorState, EmptyState, formatInr, formatPct } from "../components/Ui";

const SEGMENT_COLORS = ["#29508c", "#157a52", "#b6650a", "#7a5c17", "#a3273f", "#4b5361"];

export default function Overview() {
  const { data, loading, error, reload } = useApi(() => endpoints.overview(21), []);

  return (
    <div>
      <PageHeader
        eyebrow="Executive Overview"
        title="Revenue recovery health"
        subtitle="Live totals from the recovery database -- not demo numbers. Segment charts fall back to the last offline benchmark where noted."
      />
      <div className="space-y-6 p-8">
        {loading && <Loading label="Loading overview" />}
        {error && <ErrorState error={error} onRetry={reload} />}
        {data && <OverviewBody data={data} />}
      </div>
    </div>
  );
}

function OverviewBody({ data }) {
  const t = data.totals;
  const budget = data.budget;
  const series = data.performance_over_time || [];
  const byAction = data.recovery_by_action || [];
  const segments = data.modeled_segment_performance;

  return (
    <>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-5">
        <Kpi label="Total failed payments" value={t.total_failed_payments.toLocaleString()} />
        <Kpi label="Revenue at risk" value={formatInr(t.revenue_at_risk_inr)} tone="risk" />
        <Kpi label="Revenue recovered" value={formatInr(t.revenue_recovered_inr)} tone="recovered" />
        <Kpi label="Recovery rate" value={formatPct(t.recovery_rate)} />
        <Kpi label="Incremental value" value={formatInr(t.incremental_recovered_value_inr)} tone="action" />
        <Kpi label="Active recovery cases" value={t.active_recovery_cases.toLocaleString()} />
        <Kpi label="Pending approvals" value={t.pending_approvals.toLocaleString()} tone="pending" />
        <Kpi label="Customers contacted" value={t.customers_contacted.toLocaleString()} />
        <Kpi
          label="Recovery budget used"
          value={`${budget.contacts_used_today.toLocaleString()} / ${budget.daily_contact_budget.toLocaleString()}`}
          sub={`${budget.contacts_remaining_today.toLocaleString()} contacts remaining today`}
        />
      </div>

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-3">
        <Panel title="Recovery performance over time" className="xl:col-span-2">
          {series.length === 0 ? (
            <EmptyState label="No cases yet in this window -- run demo.sh or POST /events/payment-failed to populate it." />
          ) : (
            <ResponsiveContainer width="100%" height={260}>
              <LineChart data={series}>
                <CartesianGrid stroke="var(--line)" vertical={false} />
                <XAxis dataKey="date" tick={{ fontSize: 11, fill: "var(--muted)" }} tickLine={false} axisLine={{ stroke: "var(--line)" }} />
                <YAxis tick={{ fontSize: 11, fill: "var(--muted)" }} tickLine={false} axisLine={false} />
                <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8, borderColor: "var(--line)" }} />
                <Line type="monotone" dataKey="failed_payments" name="Failed" stroke="var(--risk)" strokeWidth={2} dot={false} />
                <Line type="monotone" dataKey="recovered_cases" name="Recovered" stroke="var(--recovered)" strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          )}
        </Panel>

        <Panel title="Recovery by action (executed)">
          {byAction.length === 0 ? (
            <EmptyState label="No executed actions yet." />
          ) : (
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={byAction} layout="vertical" margin={{ left: 24 }}>
                <CartesianGrid stroke="var(--line)" horizontal={false} />
                <XAxis type="number" tick={{ fontSize: 11, fill: "var(--muted)" }} axisLine={false} tickLine={false} />
                <YAxis type="category" dataKey="action" width={140} tick={{ fontSize: 10, fill: "var(--ink-soft)" }} axisLine={false} tickLine={false} />
                <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8, borderColor: "var(--line)" }} />
                <Bar dataKey="executions" fill="var(--action)" radius={[0, 3, 3, 0]} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </Panel>
      </div>

      {segments && (
        <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
          <Panel
            title="Recovery by customer segment (failure reason)"
            action={<span className="text-[11px]" style={{ color: "var(--muted)" }}>modeled benchmark</span>}
          >
            <ResponsiveContainer width="100%" height={240}>
              <BarChart data={segments.by_failure_reason || []}>
                <CartesianGrid stroke="var(--line)" vertical={false} />
                <XAxis dataKey="failure_reason" tick={{ fontSize: 10, fill: "var(--muted)" }} axisLine={{ stroke: "var(--line)" }} tickLine={false} interval={0} angle={-20} textAnchor="end" height={60} />
                <YAxis tick={{ fontSize: 11, fill: "var(--muted)" }} tickFormatter={(v) => `${(v * 100).toFixed(0)}%`} axisLine={false} tickLine={false} />
                <Tooltip formatter={(v) => `${(v * 100).toFixed(1)}%`} contentStyle={{ fontSize: 12, borderRadius: 8, borderColor: "var(--line)" }} />
                <Bar dataKey="recovery_rate" radius={[3, 3, 0, 0]}>
                  {(segments.by_failure_reason || []).map((_, i) => (
                    <Cell key={i} fill={SEGMENT_COLORS[i % SEGMENT_COLORS.length]} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
            <p className="mt-2 text-[11px]" style={{ color: "var(--muted)" }}>{segments.note}</p>
          </Panel>

          <Panel title="Revenue at risk vs. recovered by payment method" action={<span className="text-[11px]" style={{ color: "var(--muted)" }}>modeled benchmark</span>}>
            <ResponsiveContainer width="100%" height={240}>
              <PieChart>
                <Pie
                  data={segments.by_payment_method || []}
                  dataKey="net_value_inr"
                  nameKey="payment_method"
                  innerRadius={55}
                  outerRadius={85}
                  paddingAngle={2}
                >
                  {(segments.by_payment_method || []).map((_, i) => (
                    <Cell key={i} fill={SEGMENT_COLORS[i % SEGMENT_COLORS.length]} />
                  ))}
                </Pie>
                <Tooltip formatter={(v) => formatInr(v)} contentStyle={{ fontSize: 12, borderRadius: 8, borderColor: "var(--line)" }} />
              </PieChart>
            </ResponsiveContainer>
          </Panel>
        </div>
      )}
    </>
  );
}

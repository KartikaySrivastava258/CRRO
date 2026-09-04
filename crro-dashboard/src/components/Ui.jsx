import { AlertTriangle, Loader2, Inbox } from "lucide-react";

export function PageHeader({ eyebrow, title, subtitle, right }) {
  return (
    <div className="flex items-start justify-between border-b px-8 py-6" style={{ borderColor: "var(--line)" }}>
      <div>
        {eyebrow && (
          <div className="text-[11px] font-medium" style={{ color: "var(--muted)" }}>{eyebrow}</div>
        )}
        <h1 className="mt-0.5 text-[20px] font-semibold tracking-tight" style={{ color: "var(--ink)" }}>
          {title}
        </h1>
        {subtitle && <p className="mt-1 max-w-2xl text-[13px]" style={{ color: "var(--ink-soft)" }}>{subtitle}</p>}
      </div>
      {right}
    </div>
  );
}

export function Panel({ title, action, children, className = "" }) {
  return (
    <section
      className={"rounded-lg border bg-white " + className}
      style={{ borderColor: "var(--line)" }}
    >
      {title && (
        <header className="flex items-center justify-between border-b px-5 py-3.5" style={{ borderColor: "var(--line)" }}>
          <h2 className="text-[13px] font-semibold" style={{ color: "var(--ink)" }}>{title}</h2>
          {action}
        </header>
      )}
      <div className="p-5">{children}</div>
    </section>
  );
}

export function Loading({ label = "Loading" }) {
  return (
    <div className="flex items-center gap-2 py-16 text-[13px]" style={{ color: "var(--muted)" }}>
      <Loader2 size={15} className="animate-spin" /> {label}&hellip;
    </div>
  );
}

export function ErrorState({ error, onRetry }) {
  const detail = error?.response?.data?.detail || error?.message || "Something went wrong.";
  return (
    <div
      className="flex flex-col items-start gap-2 rounded-lg border px-5 py-4 text-[13px]"
      style={{ borderColor: "var(--blocked)", background: "var(--blocked-soft)", color: "var(--blocked)" }}
    >
      <div className="flex items-center gap-2 font-medium">
        <AlertTriangle size={15} /> Couldn&rsquo;t load this view
      </div>
      <div>{String(detail)}</div>
      {onRetry && (
        <button
          onClick={onRetry}
          className="mt-1 rounded-md border px-3 py-1.5 text-[12px] font-medium hover:bg-white/40"
          style={{ borderColor: "var(--blocked)" }}
        >
          Retry
        </button>
      )}
    </div>
  );
}

export function EmptyState({ label }) {
  return (
    <div className="flex flex-col items-center gap-2 py-14 text-[13px]" style={{ color: "var(--muted)" }}>
      <Inbox size={20} strokeWidth={1.5} />
      {label}
    </div>
  );
}

const STATUS_STYLES = {
  RECOVERED: { bg: "var(--recovered-soft)", fg: "var(--recovered)" },
  EXECUTED: { bg: "var(--recovered-soft)", fg: "var(--recovered)" },
  OPEN: { bg: "var(--action-soft)", fg: "var(--action)" },
  PENDING_APPROVAL: { bg: "var(--pending-soft)", fg: "var(--pending)" },
  REJECTED: { bg: "var(--blocked-soft)", fg: "var(--blocked)" },
  BLOCKED: { bg: "var(--blocked-soft)", fg: "var(--blocked)" },
  CLOSED_UNRECOVERED: { bg: "var(--blocked-soft)", fg: "var(--blocked)" },
  NOT_EXECUTED: { bg: "#eef0f3", fg: "var(--muted)" },
  FAILED: { bg: "var(--blocked-soft)", fg: "var(--blocked)" },
};

export function StatusBadge({ status }) {
  if (!status) return <span className="text-[12px]" style={{ color: "var(--muted)" }}>&mdash;</span>;
  const style = STATUS_STYLES[status] || { bg: "#eef0f3", fg: "var(--ink-soft)" };
  return (
    <span
      className="inline-flex items-center rounded px-2 py-0.5 text-[11px] font-medium"
      style={{ background: style.bg, color: style.fg }}
    >
      {status.replaceAll("_", " ")}
    </span>
  );
}

export function Kpi({ label, value, sub, tone }) {
  const color = tone ? `var(--${tone})` : "var(--ink)";
  return (
    <div className="rounded-lg border bg-white px-4 py-3.5" style={{ borderColor: "var(--line)" }}>
      <div className="text-[11px]" style={{ color: "var(--muted)" }}>{label}</div>
      <div className="mt-1 font-mono-tab text-[22px] font-semibold" style={{ color }}>{value}</div>
      {sub && <div className="mt-0.5 text-[11px]" style={{ color: "var(--muted)" }}>{sub}</div>}
    </div>
  );
}

export function formatInr(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return "\u2014";
  return "\u20b9" + Number(n).toLocaleString("en-IN", { maximumFractionDigits: 0 });
}

export function formatPct(n) {
  if (n === null || n === undefined) return "\u2014";
  return (Number(n) * 100).toFixed(1) + "%";
}

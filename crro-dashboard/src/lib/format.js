// One place for financial and time formatting. Money arrives as integer paise.
const inr = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", minimumFractionDigits: 2, maximumFractionDigits: 2 });
const inr0 = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });
const compact = new Intl.NumberFormat("en-IN", { notation: "compact", maximumFractionDigits: 1 });

export const isNum = (v) => typeof v === "number" && Number.isFinite(v);
export const money = (paise, { whole = false } = {}) => !isNum(paise) ? "—" : (whole ? inr0 : inr).format(paise / 100);
export const moneySigned = (paise) => !isNum(paise) ? "—" : (paise < 0 ? "−" : paise > 0 ? "+" : "") + inr0.format(Math.abs(paise) / 100);
export const moneyCompact = (paise) => !isNum(paise) ? "—" : "₹" + compact.format(paise / 100);
export const pct = (x, d = 1) => !isNum(x) ? "—" : (x * 100).toFixed(d) + "%";
export const pp = (x, d = 1) => !isNum(x) ? "—" : (x >= 0 ? "+" : "−") + Math.abs(x * 100).toFixed(d) + " pp";
export const int = (n) => !isNum(n) ? "—" : n.toLocaleString("en-IN");
export const human = (s) => (s || "").toString().replaceAll("_", " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase());

const dtf = new Intl.DateTimeFormat("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short",
  hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
export const when = (iso) => { if (!iso) return "—"; const d = new Date(iso); return isNaN(d) ? "—" : dtf.format(d) + " IST"; };
export function ago(iso) {
  if (!iso) return "—";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${Math.round(s)}s ago`; if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`; return `${Math.round(s / 86400)}d ago`;
}
export const short = (id, n = 10) => (id && id.length > n + 4 ? id.slice(0, n) + "…" : id || "—");

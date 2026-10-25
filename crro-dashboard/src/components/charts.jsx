import { isNum } from "../lib/format.js";

// Horizontal bars as HTML rows (label | track | value): crisp at any width.
// Missing values render as "n/a", never as a zero-length bar.
export function BarChart({ data, format, label, unit, highlight, note }) {
  const vals = data.filter((d) => isNum(d.value));
  if (!vals.length) return <div className="state empty">No data to chart.</div>;
  const max = Math.max(...vals.map((d) => Math.abs(d.value)), 1);
  return (
    <figure className="chart" aria-label={`${label} (${unit})`}>
      <div className="hbars" role="list">
        {data.map((d) => (
          <div key={d.label} className="hbar" role="listitem">
            <span className="hbar-name" title={d.label}>{d.label}</span>
            <span className="hbar-track"><span className={"hbar-fill" + (d.label === highlight ? " hl" : d.muted ? " muted" : "")}
              style={{ width: isNum(d.value) ? `${(Math.max(0, d.value) / max) * 100}%` : 0 }} /></span>
            <span className="hbar-val">{isNum(d.value) ? format(d.value) : "n/a"}</span>
          </div>))}
      </div>
      <figcaption>{label} · unit: {unit}{note ? ` · ${note}` : ""}</figcaption>
    </figure>
  );
}

export function Funnel({ steps }) {
  const top = Math.max(steps[0]?.value || 0, 1);
  return (
    <ol className="funnel" aria-label="Recovery funnel (case counts)">
      {steps.map((s, i) => (
        <li key={s.label}>
          <div className="funnel-label"><span>{s.label}</span><strong>{s.value.toLocaleString("en-IN")}</strong></div>
          <div className="funnel-track"><div className="funnel-fill" style={{ width: `${(s.value / top) * 100}%` }} /></div>
          {i > 0 && steps[i - 1].value > 0 && <div className="funnel-conv">{((s.value / steps[i - 1].value) * 100).toFixed(0)}% of previous</div>}
        </li>))}
    </ol>
  );
}

export function Meter({ label, used, limit, format }) {
  const frac = limit > 0 ? Math.min(1, used / limit) : 0;
  return (
    <div className="meter">
      <div className="meter-head"><span>{label}</span><span className="mono">{format(used)} / {format(limit)}</span></div>
      <div className="meter-track" role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={limit} aria-valuenow={used}>
        <div className={"meter-fill" + (frac >= 1 ? " full" : "")} style={{ width: `${frac * 100}%` }} />
      </div>
    </div>
  );
}

// Line chart with labelled axes, for budget sweeps.
export function LineChart({ points, xLabel, yLabel, formatX, formatY }) {
  const pts = points.filter((p) => isNum(p.x) && isNum(p.y));
  if (pts.length < 2) return <div className="state empty">Not enough points.</div>;
  const W = 720, H = 240, L = 64, B = 34, T = 10, R = 12;
  const xs = pts.map((p) => p.x), ys = pts.map((p) => p.y);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(0, ...ys), y1 = Math.max(...ys) || 1;
  const sx = (x) => L + ((x - x0) / (x1 - x0 || 1)) * (W - L - R);
  const sy = (y) => H - B - ((y - y0) / (y1 - y0 || 1)) * (H - B - T);
  return (
    <figure className="chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${yLabel} by ${xLabel}`} className="line-svg">
        <line x1={L} y1={H - B} x2={W - R} y2={H - B} className="ch-axis" />
        <line x1={L} y1={T} x2={L} y2={H - B} className="ch-axis" />
        {[y0, (y0 + y1) / 2, y1].map((v) => <g key={v}><text x={L - 6} y={sy(v) + 4} textAnchor="end" className="ch-tick">{formatY(v)}</text>
          <line x1={L} x2={W - R} y1={sy(v)} y2={sy(v)} className="ch-grid" /></g>)}
        {pts.map((p) => <text key={p.x} x={sx(p.x)} y={H - B + 16} textAnchor="middle" className="ch-tick">{formatX(p.x)}</text>)}
        <polyline points={pts.map((p) => `${sx(p.x)},${sy(p.y)}`).join(" ")} className="ch-line" />
        {pts.map((p) => <circle key={p.x} cx={sx(p.x)} cy={sy(p.y)} r={3.5} className="ch-dot"><title>{formatX(p.x)}: {formatY(p.y)}</title></circle>)}
        <text x={(W + L) / 2} y={H - 2} textAnchor="middle" className="ch-axis-label">{xLabel}</text>
      </svg>
      <figcaption>{yLabel} vs {xLabel}</figcaption>
    </figure>
  );
}

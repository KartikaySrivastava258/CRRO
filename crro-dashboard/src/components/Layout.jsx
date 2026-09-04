import { NavLink, Outlet } from "react-router-dom";
import {
  LayoutGrid, GitBranch, Sparkles, ScrollText, ShieldCheck, ClipboardCheck,
} from "lucide-react";

const NAV = [
  { to: "/", label: "Executive Overview", icon: LayoutGrid, end: true },
  { to: "/cases", label: "Live Recovery Case", icon: GitBranch },
  { to: "/optimizer", label: "Optimizer", icon: Sparkles },
  { to: "/explainability", label: "Explainability", icon: ShieldCheck },
  { to: "/approvals", label: "Pending Approvals", icon: ClipboardCheck },
  { to: "/audit", label: "Audit / Activity", icon: ScrollText },
];

export default function Layout() {
  return (
    <div className="flex h-screen w-full overflow-hidden" style={{ background: "var(--paper)" }}>
      <aside
        className="flex w-60 shrink-0 flex-col justify-between py-6"
        style={{ background: "var(--ink)" }}
      >
        <div>
          <div className="px-6 pb-6">
            <div className="text-[15px] font-semibold tracking-tight text-white">CRRO</div>
            <div className="mt-0.5 text-[11px] leading-snug text-white/45">
              AI Revenue Recovery Console
            </div>
          </div>
          <nav className="flex flex-col gap-0.5 px-3">
            {NAV.map(({ to, label, icon: Icon, end }) => (
              <NavLink
                key={to}
                to={to}
                end={end}
                className={({ isActive }) =>
                  "flex items-center gap-2.5 rounded-md px-3 py-2 text-[13px] transition-colors " +
                  (isActive
                    ? "bg-white/10 text-white"
                    : "text-white/55 hover:bg-white/5 hover:text-white/85")
                }
              >
                <Icon size={15} strokeWidth={1.75} />
                {label}
              </NavLink>
            ))}
          </nav>
        </div>
        <div className="px-6 text-[11px] leading-snug text-white/35">
          Detect &rarr; Understand &rarr; Predict &rarr; Optimize
          <br />
          &rarr; Approve &rarr; Execute &rarr; Verify &rarr; Recover
        </div>
      </aside>
      <main className="flex-1 overflow-y-auto">
        <Outlet />
      </main>
    </div>
  );
}

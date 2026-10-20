/** Insights: how the install and its agents are doing, on one set of tabs. */
import { NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { cn } from "../ui";
import { AgentsPage } from "./Agents";
import { AlertsPage } from "./Alerts";
import { ErrorsPage } from "./Errors";
import { MoneyPage } from "./Money";
import { OperationsPage } from "./Operations";
import { PerformancePage } from "./Performance";
import { TodayPage } from "./Today";

const TABS = [
  { to: "", label: "Today", description: "The few numbers that tell you whether your agents are working, and what needs you now." },
  { to: "agents", label: "Agents", description: "What each agent did, what it cost, and what you cleared." },
  { to: "money", label: "Money", description: "What the AI costs, by feature and model." },
  { to: "performance", label: "Performance", description: "Speed, errors and uptime of the admin and the gateway, live." },
  { to: "errors", label: "Errors", description: "Errors reported by browsers, grouped." },
  { to: "operations", label: "Operations", description: "Requests, background work and e-mails per day." },
  { to: "alerts", label: "Alerts", description: "What went wrong, and when to warn you." },
];

export function InsightsArea({ owner }: { owner: boolean }) {
  const location = useLocation();
  const sub = location.pathname.replace(/^\/insights\/?/, "").split("/")[0];
  const tab = TABS.find((t) => t.to === sub) ?? TABS[0];
  // Keep the chosen period when switching tabs.
  const keep = new URLSearchParams(location.search);
  const query = keep.get("days") ? `?days=${keep.get("days")}` : "";
  return (
    <>
      <div className="mb-4">
        <h1 className="text-lg font-semibold text-fg">Insights</h1>
        <p className="mt-0.5 max-w-3xl text-[13px] text-muted">{tab.description} Counts and timings only — never a person or what they wrote.</p>
      </div>
      <nav className="-mx-4 mb-5 flex gap-0.5 overflow-x-auto border-b border-border px-4 md:mx-0 md:px-0" aria-label="Insights">
        {TABS.map((t) => (
          <NavLink key={t.to} to={`/insights${t.to ? `/${t.to}` : ""}${query}`} end
            className={({ isActive }) => cn("-mb-px shrink-0 border-b-2 px-3 py-2 text-[13px]", isActive ? "border-primary font-medium text-fg" : "border-transparent text-muted hover:text-fg")}>
            {t.label}
          </NavLink>
        ))}
      </nav>
      <Routes>
        <Route index element={<TodayPage />} />
        <Route path="agents" element={<AgentsPage />} />
        <Route path="money" element={<MoneyPage />} />
        <Route path="performance" element={<PerformancePage />} />
        <Route path="errors" element={<ErrorsPage owner={owner} />} />
        <Route path="operations" element={<OperationsPage />} />
        <Route path="alerts" element={<AlertsPage owner={owner} />} />
        <Route path="*" element={<Navigate to="/insights" replace />} />
      </Routes>
    </>
  );
}

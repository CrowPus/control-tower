import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ArrowRight, CircleAlert, CircleX } from "lucide-react";
import { Link } from "react-router-dom";
import { api, unwrap } from "../api";
import { ago, cn, day } from "../ui";
import { FilterRow, Hero } from "./charts";
import { usePeriod, values, type AlertRow } from "./data";
import { Failed, KpiGrid, Loading, MetricChart, Note } from "./parts";

export function OpenAlerts({ alerts }: { alerts: AlertRow[] }) {
  if (!alerts.length) return null;
  const sorted = [...alerts].sort((a, b) => (a.severity === b.severity ? 0 : a.severity === "critical" ? -1 : 1));
  return (
    <div className="mb-4 space-y-1.5">
      {sorted.slice(0, 4).map((a) => {
        const critical = a.severity === "critical";
        const Icon = critical ? CircleX : CircleAlert;
        return (
          <Link key={a.id} to="/insights/alerts" className={cn("flex items-center gap-2 rounded-[var(--radius-control)] px-3 py-2 text-[13px]", critical ? "bg-danger-soft text-danger" : "bg-warning-soft text-warning")}>
            <Icon className="h-4 w-4 shrink-0" aria-hidden />
            <span className="font-medium">{critical ? "Critical" : "Warning"}:</span>
            <span className="min-w-0 flex-1 truncate">{a.subject}</span>
            <span className="hidden shrink-0 text-xs opacity-80 sm:inline">since {ago(a.opened_at)}</span>
            <ArrowRight className="h-3.5 w-3.5 shrink-0" aria-hidden />
          </Link>
        );
      })}
      {sorted.length > 4 && <Link to="/insights/alerts" className="block text-xs text-primary hover:underline">{sorted.length - 4} more open alerts</Link>}
    </div>
  );
}

export function TodayPage() {
  const [days, setDays] = usePeriod();
  const { data, error } = useQuery({
    queryKey: ["insights", "today", days],
    queryFn: async () => unwrap(await api.GET("/admin-api/insights/today", { params: { query: { days } } })),
    placeholderData: keepPreviousData,
    refetchInterval: 60_000,
  });
  if (error) return <Failed error={error} />;
  if (!data) return <Loading />;
  const x = data.activity.days;
  return (
    <>
      <FilterRow days={days} onDays={setDays} note={data.data_since ? `Counted every 15 minutes · data since ${day(data.data_since)}` : undefined} />
      <OpenAlerts alerts={data.alerts} />
      {!data.data_since && <div className="mb-4"><Note>No numbers yet. The worker counts every 15 minutes (task insights.snapshot); on its first run it fills in the past from the database.</Note></div>}
      <Hero kpi={data.north_star} days={days} x={x} />
      <KpiGrid kpis={data.kpis} days={days} className="mt-4 lg:grid-cols-3 xl:grid-cols-3" />
      <MetricChart
        className="mt-4"
        title="What the agents did each day"
        subtitle="Agent runs, and the proposals they asked clearance for."
        x={x}
        days={days}
        metric="agents.runs"
        height={240}
        series={[
          { key: "runs", label: "Runs", values: values(data.activity, "agents.runs", "*") },
          { key: "proposals", label: "Proposals", values: values(data.activity, "clearance.proposed") },
        ]}
      />
    </>
  );
}

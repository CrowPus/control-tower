import { Activity } from "lucide-react";
import type { ReactNode } from "react";
import { Alert, cn } from "../ui";
import { ChartCard, LineChart, StatTile, seriesTable, type Series } from "./charts";
import { exportHref, type Kpi } from "./data";
import { colorOf } from "./format";

/** One measure over time, with its table and its CSV. */
export function MetricChart({ title, subtitle, x, series: given, unit = "count", metric, days, height = 180, className }: { title: string; subtitle?: string; x: string[]; series: Series[]; unit?: string; metric?: string; days: number; height?: number; className?: string }) {
  // A single series is named by the chart's title.
  const series = given.length === 1 ? [{ ...given[0], label: title }] : given;
  return (
    <ChartCard title={title} subtitle={subtitle} table={seriesTable(x, series, unit)} exportHref={metric ? exportHref(metric, days) : undefined} className={className}>
      <LineChart x={x} series={series} unit={unit} height={height} label={title} />
    </ChartCard>
  );
}

const TILE_COLOR: Record<string, string> = { active_web: colorOf("web"), active_mobile: colorOf("mobile"), crashes: colorOf("mobile") };

export function KpiGrid({ kpis, days, className }: { kpis: Kpi[]; days: number; className?: string }) {
  return (
    <div className={cn("grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4", className)}>
      {kpis.map((k) => <StatTile key={k.key} kpi={k} days={days} color={TILE_COLOR[k.key]} />)}
    </div>
  );
}

export function Loading() {
  return (
    <div className="grid h-60 place-items-center text-muted" role="status" aria-label="Loading">
      <Activity className="h-4 w-4 animate-pulse" aria-hidden />
    </div>
  );
}

export function Failed({ error }: { error: Error }) {
  return <Alert>Could not load these numbers: {error.message}</Alert>;
}

export function Section({ title, description, children }: { title: string; description?: string; children: ReactNode }) {
  return (
    <section className="mt-8">
      <div className="mb-3">
        <h2 className="text-[14px] font-semibold text-fg">{title}</h2>
        {description && <p className="mt-0.5 max-w-3xl text-xs text-muted">{description}</p>}
      </div>
      {children}
    </section>
  );
}

export function Note({ children }: { children: ReactNode }) {
  return <p className="rounded-[var(--radius-card)] border border-dashed border-border bg-surface px-4 py-6 text-center text-[13px] text-muted">{children}</p>;
}

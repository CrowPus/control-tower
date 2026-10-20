/**
 * The Insights chart kit: plain SVG, no chart library. Rules (dataviz skill): thin marks,
 * 2px lines, one y-axis, a crosshair tooltip on every line chart, a table view for every
 * chart, a legend whenever there are two or more series, colour follows the entity.
 */
import { ArrowDownRight, ArrowUpRight, CircleAlert, CircleCheck, CircleX, Download, Info, LineChart as LineIcon, Minus, Table2 } from "lucide-react";
import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent, type ReactNode } from "react";
import { cn } from "../ui";
import { axisLabel, delta, fmt, tooltipLabel } from "./format";

// --- sizing ----------------------------------------------------------------------------------------
export function useWidth<T extends HTMLElement>(): [React.RefObject<T | null>, number] {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    setWidth(el.clientWidth);
    const observer = new ResizeObserver(([entry]) => setWidth(Math.floor(entry.contentRect.width)));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}

/** A round top for the y-axis and 3–4 gridlines on round numbers. */
export function niceScale(max: number, ticks = 4, whole = false): { top: number; step: number } {
  if (!(max > 0)) return whole ? { top: 1, step: 1 } : { top: 1, step: 0.25 };
  const raw = whole ? Math.max(1, max / ticks) : max / ticks;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => s >= raw) ?? 10 * magnitude;
  return { top: Math.ceil(max / step) * step, step };
}

function pathOf(points: ({ x: number; y: number } | null)[]): string {
  let d = "";
  let pen = false;
  for (const p of points) {
    if (!p) {
      pen = false;
      continue;
    }
    d += `${pen ? "L" : "M"}${p.x.toFixed(1)},${p.y.toFixed(1)}`;
    pen = true;
  }
  return d;
}

// --- line chart ------------------------------------------------------------------------------------
export interface Series {
  key: string;
  label: string;
  values: (number | null)[];
  color?: string;
}

export function LineChart({ x, series, unit = "count", height = 220, hourly = false, area, label }: { x: string[]; series: Series[]; unit?: string; height?: number; hourly?: boolean; area?: boolean; label: string }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [active, setActive] = useState<number | null>(null);
  const gradient = useId();
  const multi = series.length > 1;
  const fill = area ?? !multi;
  const margin = { top: 10, right: multi ? 84 : 12, bottom: 26, left: 48 };
  const plotW = Math.max(10, width - margin.left - margin.right);
  const plotH = height - margin.top - margin.bottom;
  const max = Math.max(0, ...series.flatMap((s) => s.values.map((v) => v ?? 0)));
  const { top, step } = niceScale(max, 4, unit === "count" || unit === "eur_cents");
  const n = x.length;
  const px = useCallback((i: number) => margin.left + (n <= 1 ? plotW / 2 : (i / (n - 1)) * plotW), [margin.left, n, plotW]);
  const py = (v: number) => margin.top + plotH - (v / top) * plotH;
  const ticks = Array.from({ length: Math.round(top / step) + 1 }, (_, i) => i * step);
  const xTicks = useMemo(() => {
    const count = Math.max(2, Math.min(6, Math.floor(plotW / 90)));
    if (n <= count) return x.map((_, i) => i);
    return Array.from({ length: count }, (_, i) => Math.round((i * (n - 1)) / (count - 1)));
  }, [n, plotW, x]);

  // End labels, pushed apart so they never collide.
  const ends = useMemo(() => {
    if (!multi) return [];
    const items = series.map((s) => {
      let i = s.values.length - 1;
      while (i > 0 && s.values[i] === null) i--;
      return { key: s.key, label: s.label, color: s.color, y: py(s.values[i] ?? 0) };
    });
    items.sort((a, b) => a.y - b.y);
    for (let i = 1; i < items.length; i++) items[i].y = Math.max(items[i].y, items[i - 1].y + 13);
    return items;
  }, [series, top, plotH, multi]);

  const move = (event: PointerEvent<SVGRectElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    const ratio = (event.clientX - box.left) / box.width;
    setActive(Math.max(0, Math.min(n - 1, Math.round(ratio * (n - 1)))));
  };
  const key = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "ArrowRight") setActive((a) => Math.min(n - 1, (a ?? -1) + 1));
    else if (event.key === "ArrowLeft") setActive((a) => Math.max(0, (a ?? n) - 1));
    else if (event.key === "Escape") setActive(null);
    else return;
    event.preventDefault();
  };

  const tipLeft = active === null ? 0 : px(active);
  const flip = tipLeft > width * 0.6;
  return (
    <div ref={ref} className="relative outline-none" tabIndex={0} onKeyDown={key} onBlur={() => setActive(null)} role="img" aria-label={`${label}. Use the arrow keys to read each day, or switch to the table.`}>
      {multi && (
        <ul className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
          {series.map((s) => (
            <li key={s.key} className="flex items-center gap-1.5"><span className="h-[3px] w-3.5 rounded-full" style={{ background: s.color ?? "var(--viz-1)" }} />{s.label}</li>
          ))}
        </ul>
      )}
      {width > 0 && (
        <svg width={width} height={height} className="block overflow-visible">
          {fill && (
            <defs>
              <linearGradient id={gradient} x1="0" x2="0" y1="0" y2="1">
                <stop offset="0%" stopColor={series[0]?.color ?? "var(--viz-1)"} stopOpacity={0.18} />
                <stop offset="100%" stopColor={series[0]?.color ?? "var(--viz-1)"} stopOpacity={0} />
              </linearGradient>
            </defs>
          )}
          {ticks.map((t) => (
            <g key={t}>
              <line x1={margin.left} x2={margin.left + plotW} y1={py(t)} y2={py(t)} stroke="var(--viz-grid)" strokeWidth={1} />
              <text x={margin.left - 8} y={py(t)} dy="0.32em" textAnchor="end" className="fill-[var(--viz-axis)] text-[10.5px]">{fmt(t, unit, true)}</text>
            </g>
          ))}
          {xTicks.map((i) => (
            <text key={i} x={px(i)} y={height - 6} textAnchor={i === 0 ? "start" : i === n - 1 ? "end" : "middle"} className="fill-[var(--viz-axis)] text-[10.5px]">{axisLabel(x[i], hourly)}</text>
          ))}
          {series.map((s, si) => {
            const points = s.values.map((v, i) => (v === null ? null : { x: px(i), y: py(v) }));
            const line = pathOf(points);
            return (
              <g key={s.key}>
                {fill && si === 0 && line && <path d={`${line}L${px(n - 1).toFixed(1)},${py(0)}L${px(0).toFixed(1)},${py(0)}Z`} fill={`url(#${gradient})`} />}
                <path d={line} fill="none" stroke={s.color ?? "var(--viz-1)"} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
              </g>
            );
          })}
          {ends.map((e) => (
            <text key={e.key} x={margin.left + plotW + 8} y={e.y} dy="0.32em" className="fill-[var(--fg-muted)] text-[11px]">
              <tspan fill={e.color ?? "var(--viz-1)"}>●</tspan> {e.label.length > 11 ? `${e.label.slice(0, 10)}…` : e.label}
            </text>
          ))}
          {active !== null && (
            <g pointerEvents="none">
              <line x1={px(active)} x2={px(active)} y1={margin.top} y2={margin.top + plotH} stroke="var(--viz-crosshair)" strokeWidth={1} />
              {series.map((s) =>
                s.values[active] === null ? null : <circle key={s.key} cx={px(active)} cy={py(s.values[active] ?? 0)} r={4} fill={s.color ?? "var(--viz-1)"} stroke="var(--surface)" strokeWidth={2} />,
              )}
            </g>
          )}
          <rect x={margin.left} y={0} width={plotW} height={height} fill="transparent" onPointerMove={move} onPointerLeave={() => setActive(null)} />
        </svg>
      )}
      {active !== null && (
        <div className="pointer-events-none absolute z-10 min-w-[150px] rounded-[var(--radius-control)] border border-border bg-overlay px-3 py-2 text-xs shadow-[var(--shadow)]"
          style={{ top: multi ? 28 : 4, left: flip ? undefined : tipLeft + 12, right: flip ? width - tipLeft + 12 : undefined }}>
          <p className="mb-1 font-medium text-fg">{tooltipLabel(x[active], hourly)}</p>
          {series.map((s) => (
            <p key={s.key} className="flex items-center justify-between gap-4 text-muted">
              <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full" style={{ background: s.color ?? "var(--viz-1)" }} />{s.label}</span>
              <span className="font-medium tabular-nums text-fg">{fmt(s.values[active], unit)}</span>
            </p>
          ))}
        </div>
      )}
    </div>
  );
}

// --- sparkline -------------------------------------------------------------------------------------
export function Sparkline({ values, color = "var(--viz-1)", height = 34 }: { values: number[]; color?: string; height?: number }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const gradient = useId();
  // The shape matters in a sparkline, not the zero line: scale from the lowest value.
  const max = Math.max(...values, 0);
  const min = Math.min(...values, max);
  const span = max - min;
  const n = values.length;
  const points = values.map((v, i) => ({ x: n <= 1 ? width / 2 : (i / (n - 1)) * width, y: 2 + (height - 4) * (1 - (span ? (v - min) / span : 0.5)) }));
  const line = pathOf(points);
  return (
    <div ref={ref} aria-hidden className="h-[34px]" style={{ height }}>
      {width > 0 && n > 1 && (
        <svg width={width} height={height} className="block">
          <defs>
            <linearGradient id={gradient} x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity={0.16} />
              <stop offset="100%" stopColor={color} stopOpacity={0} />
            </linearGradient>
          </defs>
          <path d={`${line}L${width},${height}L0,${height}Z`} fill={`url(#${gradient})`} />
          <path d={line} fill="none" stroke={color} strokeWidth={1.75} strokeLinejoin="round" strokeLinecap="round" />
        </svg>
      )}
    </div>
  );
}

// --- numbers -----------------------------------------------------------------------------------------
export interface KpiLike {
  key: string;
  label: string;
  unit: string;
  good: string;
  hint?: string;
  value: number | null;
  previous: number | null;
  spark: number[];
  has_data: boolean;
}

export function Delta({ value, previous, good, days }: { value: number | null; previous: number | null; good: string; days: number }) {
  const d = delta(value, previous, good);
  if (!d) return <span className="text-xs text-subtle">no earlier data</span>;
  const Icon = d.direction === "up" ? ArrowUpRight : d.direction === "down" ? ArrowDownRight : Minus;
  return (
    <span className="inline-flex items-center gap-1 text-xs">
      <span className={cn("inline-flex items-center gap-0.5 rounded-full px-1.5 py-px font-medium", d.tone === "good" ? "bg-success-soft text-success" : d.tone === "bad" ? "bg-danger-soft text-danger" : "bg-raised text-muted")}>
        <Icon className="h-3 w-3" aria-hidden />
        {d.text}
      </span>
      <span className="text-subtle">vs previous {days} d</span>
    </span>
  );
}

export function StatTile({ kpi, days, color }: { kpi: KpiLike; days: number; color?: string }) {
  return (
    <div data-nia={`kpi:${kpi.key}`} className="flex min-w-0 flex-col rounded-[var(--radius-card)] border border-border bg-surface px-4 pb-2 pt-3">
      <p className="flex items-center gap-1 truncate text-xs text-muted" title={kpi.hint || undefined}>
        {kpi.label}
        {kpi.hint && <Info className="h-3 w-3 shrink-0 text-subtle" aria-label={kpi.hint} />}
      </p>
      <p className="mt-1 text-[22px] font-semibold leading-tight tracking-tight text-fg">{kpi.has_data ? fmt(kpi.value, kpi.unit) : "—"}</p>
      <div className="mt-1 min-h-[20px]">{kpi.has_data ? <Delta value={kpi.value} previous={kpi.previous} good={kpi.good} days={days} /> : <span className="text-xs text-subtle">no data yet</span>}</div>
      <div className="mt-auto pt-1.5"><Sparkline values={kpi.spark} color={color} /></div>
    </div>
  );
}

export function Hero({ kpi, days, x, children }: { kpi: KpiLike; days: number; x: string[]; children?: ReactNode }) {
  return (
    <section className="rounded-[var(--radius-card)] border border-border bg-surface p-5">
      <div className="grid gap-6 lg:grid-cols-[minmax(220px,300px)_1fr]">
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-primary">North star</p>
          <h2 className="mt-1 text-[15px] font-semibold text-fg">{kpi.label}</h2>
          <p className="mt-3 text-5xl font-semibold tracking-tight text-fg">{kpi.has_data ? fmt(kpi.value, kpi.unit) : "—"}</p>
          <p className="mt-1 text-xs text-muted">in the last {days} days</p>
          <div className="mt-3">{kpi.has_data && <Delta value={kpi.value} previous={kpi.previous} good={kpi.good} days={days} />}</div>
          {kpi.hint && <p className="mt-4 text-[13px] text-muted">{kpi.hint}</p>}
          {children}
        </div>
        <LineChart x={x} series={[{ key: kpi.key, label: kpi.label, values: kpi.spark }]} unit={kpi.unit} height={200} label={`${kpi.label} per day`} />
      </div>
    </section>
  );
}

// --- cards with a table view ------------------------------------------------------------------------
export interface TableView {
  head: string[];
  rows: (string | number)[][];
}

export function ChartCard({ title, subtitle, table, exportHref, children, className, actions }: { title: string; subtitle?: string; table?: TableView; exportHref?: string; children: ReactNode; className?: string; actions?: ReactNode }) {
  const [asTable, setAsTable] = useState(false);
  return (
    <section className={cn("min-w-0 rounded-[var(--radius-card)] border border-border bg-surface", className)}>
      <header className="flex items-start justify-between gap-3 px-4 pb-1 pt-3">
        <div className="min-w-0">
          <h2 className="text-[13px] font-semibold text-fg">{title}</h2>
          {subtitle && <p className="mt-0.5 text-xs text-muted">{subtitle}</p>}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {actions}
          {table && (
            <button type="button" onClick={() => setAsTable((v) => !v)} className="rounded p-1 text-subtle hover:bg-raised hover:text-fg" title={asTable ? "Show the chart" : "Show as a table"} aria-pressed={asTable}>
              {asTable ? <LineIcon className="h-4 w-4" /> : <Table2 className="h-4 w-4" />}
              <span className="sr-only">{asTable ? "Show the chart" : "Show as a table"}</span>
            </button>
          )}
          {exportHref && (
            <a href={exportHref} className="rounded p-1 text-subtle hover:bg-raised hover:text-fg" title="Download as CSV">
              <Download className="h-4 w-4" />
              <span className="sr-only">Download as CSV</span>
            </a>
          )}
        </div>
      </header>
      <div className="px-4 pb-4 pt-2">{asTable && table ? <DataTable table={table} /> : children}</div>
    </section>
  );
}

export function DataTable({ table, max = 400 }: { table: TableView; max?: number }) {
  return (
    <div className="max-h-[360px] overflow-auto">
      <table className="w-full text-xs">
        <thead className="sticky top-0 bg-surface">
          <tr className="border-b border-border text-left text-muted">
            {table.head.map((h, i) => <th key={h} className={cn("whitespace-nowrap py-1.5 pr-3 font-medium", i > 0 && "text-right")}>{h}</th>)}
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {table.rows.slice(0, max).map((row, r) => (
            <tr key={r}>
              {row.map((cell, i) => <td key={i} className={cn("py-1 pr-3 text-fg", i > 0 && "text-right tabular-nums")}>{cell}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** The table view of a line chart: one row per day, one column per series. */
export function seriesTable(x: string[], series: Series[], unit: string, hourly = false): TableView {
  return { head: [hourly ? "Time" : "Day", ...series.map((s) => s.label)], rows: x.map((d, i) => [tooltipLabel(d, hourly), ...series.map((s) => fmt(s.values[i], unit))]).reverse() };
}

// --- bars --------------------------------------------------------------------------------------------
/** Ranked horizontal bars in one hue (magnitude is length, never shade). */
export function BarList({ items, unit = "count", color = "var(--viz-1)", label = (k: string) => k, limit = 10, empty = "Nothing yet." }: { items: { dimension: string; value: number }[]; unit?: string; color?: string; label?: (key: string) => string; limit?: number; empty?: string }) {
  const [all, setAll] = useState(false);
  if (!items.length) return <p className="py-6 text-center text-xs text-muted">{empty}</p>;
  const max = Math.max(...items.map((i) => i.value), 0) || 1;
  const shown = all ? items : items.slice(0, limit);
  return (
    <div>
      <ul className="space-y-1.5">
        {shown.map((item) => (
          <li key={item.dimension} className="group grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-0.5" title={`${label(item.dimension)}: ${fmt(item.value, unit)}`}>
            <span className="truncate text-xs text-fg">{label(item.dimension)}</span>
            <span className="text-right text-xs font-medium tabular-nums text-fg">{fmt(item.value, unit)}</span>
            <span className="col-span-2 h-1.5 overflow-hidden rounded-full bg-raised">
              <span className="block h-full rounded-full transition-[width] group-hover:opacity-80" style={{ width: `${Math.max(1.5, (item.value / max) * 100)}%`, background: color }} />
            </span>
          </li>
        ))}
      </ul>
      {items.length > limit && (
        <button type="button" onClick={() => setAll((v) => !v)} className="mt-2 text-xs text-primary hover:underline">{all ? "Show fewer" : `Show all ${items.length}`}</button>
      )}
    </div>
  );
}

export const barTable = (items: { dimension: string; value: number }[], head: [string, string], unit = "count", label = (k: string) => k): TableView => ({
  head,
  rows: items.map((i) => [label(i.dimension), fmt(i.value, unit)]),
});

// --- funnel ------------------------------------------------------------------------------------------
/** Steps as bars from the first step's width; between steps, the share who went on. */
export function Funnel({ steps, label }: { steps: { step: string; value: number }[]; label: (key: string) => string }) {
  const first = steps[0]?.value || 0;
  if (!first) return <p className="py-6 text-center text-xs text-muted">No one in this funnel yet.</p>;
  // The one step where the largest share drops out.
  const rates = steps.map((s, i) => (i && steps[i - 1].value ? s.value / steps[i - 1].value : 1));
  const worst = rates.indexOf(Math.min(...rates));
  return (
    <ol className="space-y-1">
      {steps.map((s, i) => {
        const prev = i ? steps[i - 1].value : null;
        const rate = prev ? s.value / prev : null;
        const weak = i === worst && rate !== null && rate < 1;
        return (
          <li key={s.step}>
            {rate !== null && (
              <p className={cn("ml-1 flex items-center gap-1 py-0.5 text-[11px]", weak ? "text-warning" : "text-subtle")}>
                {weak ? <CircleAlert className="h-3 w-3" aria-hidden /> : <ArrowDownRight className="h-3 w-3" aria-hidden />}
                {fmt(rate, "share")} went on{weak && " — the biggest drop"}
              </p>
            )}
            <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3">
              <div className="relative h-7 overflow-hidden rounded-[6px] bg-raised">
                <div className="absolute inset-y-0 left-0 rounded-[6px]" style={{ width: `${Math.max(0.8, (s.value / first) * 100)}%`, background: "var(--viz-1)", opacity: 0.85 - i * (0.5 / steps.length) }} />
                <span className="absolute inset-y-0 left-2 flex items-center text-xs font-medium text-fg [text-shadow:0_0_6px_var(--surface)]">{label(s.step)}</span>
              </div>
              <span className="w-24 text-right text-xs tabular-nums text-fg"><b className="font-semibold">{fmt(s.value)}</b> <span className="text-subtle">· {fmt(s.value / first, "share")}</span></span>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

// --- heat table (cohorts, retention) -----------------------------------------------------------------
/** Each cell's share shaded in one hue, the number always printed (never colour alone). */
export function HeatTable({ head, rows }: { head: string[]; rows: { label: string; cells: { share: number | null; text: string; title?: string }[] }[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-separate border-spacing-[2px] text-xs">
        <thead>
          <tr className="text-muted">
            {head.map((h, i) => <th key={h} className={cn("whitespace-nowrap px-1.5 py-1 font-medium", i === 0 ? "text-left" : "text-center")}>{h}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.label}>
              <th className="whitespace-nowrap px-1.5 py-1 text-left font-normal text-muted">{row.label}</th>
              {row.cells.map((c, i) => (
                <td key={i} title={c.title} className="relative min-w-[52px] rounded-[4px] px-1.5 py-1.5 text-center tabular-nums text-fg">
                  {c.share !== null && <span aria-hidden className="absolute inset-0 rounded-[4px]" style={{ background: "var(--viz-1)", opacity: 0.06 + Math.min(1, c.share) * 0.44 }} />}
                  <span className="relative">{c.text}</span>
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// --- status ------------------------------------------------------------------------------------------
export type Health = "good" | "needs_work" | "poor" | "none";

/** Status always as colour + icon + word. */
export function HealthBadge({ health, children }: { health: Health; children?: ReactNode }) {
  const map = {
    good: { Icon: CircleCheck, cls: "bg-success-soft text-success", text: "Good" },
    needs_work: { Icon: CircleAlert, cls: "bg-warning-soft text-warning", text: "Needs work" },
    poor: { Icon: CircleX, cls: "bg-danger-soft text-danger", text: "Poor" },
    none: { Icon: Minus, cls: "bg-raised text-muted", text: "No rating" },
  }[health];
  return (
    <span className={cn("inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-medium", map.cls)}>
      <map.Icon className="h-3 w-3" aria-hidden />
      {children ?? map.text}
    </span>
  );
}

/** The last checks of one address as a strip: a green tick per success, a red block per failure. */
export function UptimeStrip({ checks }: { checks: { at: string; ok: boolean; ms: number }[] }) {
  return (
    <div className="flex h-6 items-stretch gap-[2px]" role="img" aria-label={`${checks.filter((c) => c.ok).length} of ${checks.length} recent checks succeeded`}>
      {checks.map((c) => (
        <span key={c.at} title={`${tooltipLabel(c.at, true)} · ${c.ok ? `OK, ${fmt(c.ms, "ms")}` : "failed"}`} className={cn("min-w-[3px] flex-1 rounded-[2px]", c.ok ? "bg-success/70" : "bg-danger")} />
      ))}
    </div>
  );
}

// --- page chrome -------------------------------------------------------------------------------------
export const RANGES = [7, 30, 90, 365] as const;

/** The one filter row above the charts: the period, and page actions. */
export function FilterRow({ days, onDays, options = RANGES, unit = "d", children, note }: { days: number; onDays: (d: number) => void; options?: readonly number[]; unit?: "d" | "h" | "w"; children?: ReactNode; note?: ReactNode }) {
  const label = (d: number) => (unit === "w" ? `${d} weeks` : unit === "h" ? (d < 24 ? `${d} h` : `${d / 24} d`) : d === 365 ? "12 months" : `${d} days`);
  return (
    <div className="mb-4 flex flex-wrap items-center gap-3">
      <div className="inline-flex rounded-[var(--radius-control)] border border-border bg-surface p-0.5" role="radiogroup" aria-label="Period">
        {options.map((d) => (
          <button key={d} type="button" role="radio" aria-checked={days === d} onClick={() => onDays(d)}
            className={cn("rounded-[calc(var(--radius-control)-2px)] px-3 py-1 text-xs", days === d ? "bg-primary font-medium text-primary-fg" : "text-muted hover:text-fg")}>
            {label(d)}
          </button>
        ))}
      </div>
      {note && <span className="text-xs text-subtle">{note}</span>}
      <div className="ml-auto flex items-center gap-2">{children}</div>
    </div>
  );
}

/** A tick that re-renders relative times every 30 s. */
export function useNow(every = 30_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), every);
    return () => window.clearInterval(t);
  }, [every]);
  return now;
}

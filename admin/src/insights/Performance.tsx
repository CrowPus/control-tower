import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { api, unwrap } from "../api";
import { ago, cn } from "../ui";
import { ChartCard, DataTable, FilterRow, HealthBadge, LineChart, Sparkline, UptimeStrip, seriesTable, type Health, type Series } from "./charts";
import { usePeriod } from "./data";
import { colorOf, fmt, nameOf } from "./format";
import { Failed, Loading, Note, Section } from "./parts";

type Row = Record<string, unknown>;
const num = (v: unknown): number | null => (typeof v === "number" ? v : null);
const str = (v: unknown): string => (v === null || v === undefined ? "" : String(v));

const MAIN_APPS = ["admin", "gateway"];

function errorHealth(rate: number, count: number): Health {
  if (!count) return "none";
  return rate < 0.005 ? "good" : rate < 0.02 ? "needs_work" : "poor";
}
function speedHealth(p95: number | null): Health {
  if (p95 === null) return "none";
  return p95 <= 800 ? "good" : p95 <= 2000 ? "needs_work" : "poor";
}

const VITAL_INFO: Record<string, { label: string; what: string }> = {
  LCP: { label: "Largest paint", what: "How long until the main content shows" },
  INP: { label: "Response to input", what: "How long a tap or click takes to show a reaction" },
  CLS: { label: "Layout shift", what: "How much the page jumps while loading (no unit)" },
  FCP: { label: "First paint", what: "How long until anything shows" },
  TTFB: { label: "First byte", what: "How long the server takes to start answering" },
};
const vitalValue = (name: string, v: number | null) => (v === null ? "—" : name === "CLS" ? v.toFixed(3) : fmt(v, "ms"));

function Vitals({ vitals }: { vitals: Row }) {
  const summary = (vitals.summary as Row[]) ?? [];
  const pages = ((vitals.pages as Row[]) ?? []).filter((p) => str(p.name) in VITAL_INFO);
  const devices = (vitals.devices as Row[]) ?? [];
  const order = Object.keys(VITAL_INFO);
  const web = summary.filter((s) => str(s.name) in VITAL_INFO).sort((a, b) => order.indexOf(str(a.name)) - order.indexOf(str(b.name)));
  const apps = [...new Set(web.map((s) => str(s.app)))];
  if (!apps.length) return <Note>No page-speed measurements yet. Browsers send them once the web telemetry is live (anonymous, no cookies).</Note>;
  return (
    <div className="space-y-4">
      <div className="grid gap-4 xl:grid-cols-3">
        {apps.map((app) => (
          <ChartCard key={app} title={nameOf(app)} subtitle="75th percentile: 3 of 4 visits are this fast or faster"
            table={{ head: ["Measure", "p75", "Samples", "Fast visits", "Rating"], rows: web.filter((s) => s.app === app).map((s) => [str(s.name), vitalValue(str(s.name), num(s.p75)), fmt(num(s.samples)), fmt(num(s.good_share), "share"), nameOf(str(s.rating))]) }}>
            <ul className="divide-y divide-border">
              {web.filter((s) => s.app === app).map((s) => {
                const name = str(s.name);
                const share = num(s.good_share);
                return (
                  <li key={name} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 py-2">
                    <span className="min-w-0">
                      <span className="text-xs font-medium text-fg">{VITAL_INFO[name].label}</span> <span className="text-[11px] text-subtle">{name}</span>
                      <span className="block truncate text-[11px] text-muted">{VITAL_INFO[name].what}</span>
                    </span>
                    <span className="flex items-center gap-2">
                      <span className="text-sm font-semibold tabular-nums text-fg">{vitalValue(name, num(s.p75))}</span>
                      <HealthBadge health={str(s.rating) as Health} />
                    </span>
                    {share !== null && (
                      <span className="col-span-2 flex items-center gap-2 text-[11px] text-muted">
                        <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-raised"><span className="block h-full rounded-full" style={{ width: `${share * 100}%`, background: colorOf(app) }} /></span>
                        {fmt(share, "share")} fast · {fmt(num(s.samples))} visits
                      </span>
                    )}
                  </li>
                );
              })}
            </ul>
          </ChartCard>
        ))}
      </div>
      <div className="grid gap-4 xl:grid-cols-[3fr_2fr]">
        <ChartCard title="Pages to look at first" subtitle="Slowest pages by measure (at least 3 visits); ids are replaced by :id">
          {pages.length ? (
            <DataTable table={{ head: ["Page", "Measure", "p75", "Visits", "Rating"], rows: pages.map((p) => [`${nameOf(str(p.app))} · ${str(p.page) || "/"}`, str(p.name), vitalValue(str(p.name), num(p.p75)), fmt(num(p.samples)), nameOf(str(p.rating))]) }} />
          ) : <Note>Not enough visits per page yet.</Note>}
        </ChartCard>
        <ChartCard title="Phones against computers" subtitle="Largest paint (LCP) at the 75th percentile">
          <DataTable table={{ head: ["App", "Device", "LCP p75", "Visits"], rows: devices.filter((d) => d.name === "LCP").map((d) => [nameOf(str(d.app)), nameOf(str(d.device) || "unknown"), vitalValue("LCP", num(d.p75)), fmt(num(d.samples))]) }} />
        </ChartCard>
      </div>
    </div>
  );
}

export function PerformancePage() {
  const [hours, setHours] = usePeriod("hours", 24);
  const { data, error, dataUpdatedAt } = useQuery({
    queryKey: ["insights", "performance", hours],
    queryFn: async () => unwrap(await api.GET("/admin-api/insights/performance", { params: { query: { hours } } })),
    placeholderData: keepPreviousData,
    refetchInterval: 60_000,
  });
  if (error) return <Failed error={error} />;
  if (!data) return <Loading />;
  const hourly = hours <= 72;
  const apps = data.apps as Row[];
  const timeline = (app: Row) => (app.timeline as Row[]) ?? [];
  const main = apps.filter((a) => MAIN_APPS.includes(str(a.app)));
  const others = apps.filter((a) => !MAIN_APPS.includes(str(a.app)));
  const requests: Series[] = main.map((a) => ({ key: str(a.app), label: nameOf(str(a.app)) === "Member web app" ? "Web" : nameOf(str(a.app)), color: colorOf(str(a.app)), values: timeline(a).map((t) => num(t.count) ?? 0) }));
  if (others.length) requests.push({ key: "other", label: "Other", color: "var(--viz-neutral)", values: data.axis.map((_, i) => others.reduce((n, a) => n + (num(timeline(a)[i]?.count) ?? 0), 0)) });
  const p95: Series[] = main.map((a) => ({ key: str(a.app), label: nameOf(str(a.app)) === "Member web app" ? "Web" : nameOf(str(a.app)), color: colorOf(str(a.app)), values: timeline(a).map((t) => num(t.p95)) }));
  const errors: Series[] = [{ key: "errors", label: "Server errors", values: data.axis.map((_, i) => apps.reduce((n, a) => n + (num(timeline(a)[i]?.errors) ?? 0), 0)) }];
  return (
    <>
      <FilterRow days={hours} onDays={setHours} options={[6, 24, 72, 168, 720]} unit="h" note={`Live from the servers · refreshed ${ago(new Date(dataUpdatedAt).toISOString())}`} />
      <ChartCard title="Each part" subtitle="Requests answered, server errors and speed (p95: 19 of 20 requests are faster)">
        {apps.length ? (
          <div className="-mx-4 overflow-x-auto">
            <table className="w-full text-[13px]">
              <thead>
                <tr className="border-b border-border text-left text-xs text-muted">
                  <th className="px-4 py-2 font-medium">Part</th><th className="px-3 py-2 text-right font-medium">Requests</th><th className="px-3 py-2 font-medium">Server errors</th>
                  <th className="px-3 py-2 text-right font-medium">p50</th><th className="px-3 py-2 font-medium">p95</th><th className="w-[28%] px-4 py-2 font-medium">Requests over time</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {apps.map((a) => {
                  const count = num(a.count) ?? 0;
                  const rate = num(a.error_rate) ?? 0;
                  return (
                    <tr key={str(a.app)}>
                      <td className="px-4 py-2"><span className="flex items-center gap-2"><span className="h-2 w-2 rounded-full" style={{ background: MAIN_APPS.includes(str(a.app)) ? colorOf(str(a.app)) : "var(--viz-neutral)" }} />{nameOf(str(a.app))}</span></td>
                      <td className="px-3 py-2 text-right tabular-nums">{fmt(count)}</td>
                      <td className="px-3 py-2"><HealthBadge health={errorHealth(rate, count)}>{fmt(num(a.errors))} · {fmt(rate, "share")}</HealthBadge></td>
                      <td className="px-3 py-2 text-right tabular-nums">{fmt(num(a.p50), "ms")}</td>
                      <td className="px-3 py-2"><HealthBadge health={speedHealth(num(a.p95))}>{fmt(num(a.p95), "ms")}</HealthBadge></td>
                      <td className="px-4 py-1"><Sparkline values={timeline(a).map((t) => num(t.count) ?? 0)} color={MAIN_APPS.includes(str(a.app)) ? colorOf(str(a.app)) : "var(--viz-neutral)"} height={28} /></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : <Note>No requests counted in this period yet. Servers write their counts every minute.</Note>}
      </ChartCard>
      <div className="mt-4 grid gap-4 xl:grid-cols-3">
        <ChartCard title="Requests" subtitle={hourly ? "Per hour" : "Per day"} table={seriesTable(data.axis, requests, "count", hourly)}>
          <LineChart x={data.axis} series={requests} hourly={hourly} label="Requests per part" />
        </ChartCard>
        <ChartCard title="Speed (p95)" subtitle="Lower is better" table={seriesTable(data.axis, p95, "ms", hourly)}>
          <LineChart x={data.axis} series={p95} unit="ms" hourly={hourly} label="95th percentile response time" />
        </ChartCard>
        <ChartCard title="Server errors" subtitle="Answers with status 500 or higher" table={seriesTable(data.axis, errors, "count", hourly)}>
          <LineChart x={data.axis} series={errors} hourly={hourly} label="Server errors" />
        </ChartCard>
      </div>
      <Section title="Reachable from outside" description="Every minute the worker opens each public address like a visitor would.">
        <ChartCard title="Uptime" table={{ head: ["Address", "Checks", "Up", "Average answer", "Last status"], rows: (data.uptime as Row[]).map((u) => [str(u.target), fmt(num(u.checks)), fmt(num(u.ok_share), "share"), fmt(num(u.avg_ms), "ms"), str(u.last_status)]) }}>
          {(data.uptime as Row[]).length ? (
            <ul className="divide-y divide-border">
              {(data.uptime as Row[]).map((u) => {
                const share = num(u.ok_share);
                return (
                  <li key={str(u.target)} className="grid items-center gap-2 py-2.5 sm:grid-cols-[200px_1fr_auto]">
                    <span className="flex items-center gap-2 text-[13px] text-fg">
                      <HealthBadge health={u.last_ok ? "good" : "poor"}>{u.last_ok ? "Up" : "Down"}</HealthBadge>
                      {str(u.target)}
                    </span>
                    <UptimeStrip checks={(u.recent as { at: string; ok: boolean; ms: number }[]) ?? []} />
                    <span className={cn("text-right text-xs tabular-nums", share !== null && share < 0.99 ? "text-danger" : "text-muted")}>{fmt(share, "share")} up · {fmt(num(u.avg_ms), "ms")}</span>
                  </li>
                );
              })}
            </ul>
          ) : <Note>No checks yet. The worker task insights.uptime checks every minute.</Note>}
        </ChartCard>
      </Section>
      <Section title="Slow and busy routes" description="Addresses as templates ({id} instead of real ids). Fix the slow ones that are also busy first.">
        <div className="grid gap-4 xl:grid-cols-2">
          <ChartCard title="Slowest (p95)">
            <DataTable table={{ head: ["Route", "p95", "Average", "Requests", "Errors"], rows: (data.slowest as Row[]).map((r) => [`${nameOf(str(r.app))} · ${str(r.route)}`, fmt(num(r.p95), "ms"), fmt(num(r.avg), "ms"), fmt(num(r.count)), fmt(num(r.errors))]) }} />
          </ChartCard>
          <ChartCard title="Busiest">
            <DataTable table={{ head: ["Route", "Requests", "p95", "Errors"], rows: (data.busiest as Row[]).map((r) => [`${nameOf(str(r.app))} · ${str(r.route)}`, fmt(num(r.count)), fmt(num(r.p95), "ms"), fmt(num(r.errors))]) }} />
          </ChartCard>
        </div>
      </Section>
      <Section title="Speed in the browser" description={`Google's Core Web Vitals, measured in real visitors' browsers over the last ${num(data.vitals.days) === 1 ? "day" : `${fmt(num(data.vitals.days))} days`}. Green is what Google calls good.`}>
        <Vitals vitals={data.vitals} />
      </Section>
    </>
  );
}

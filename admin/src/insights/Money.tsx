import { BarList, ChartCard, DataTable, FilterRow, barTable } from "./charts";
import { useGroup, usePeriod, values } from "./data";
import { fmt, nameOf } from "./format";
import { Failed, KpiGrid, Loading, MetricChart, Section } from "./parts";

// AI operations are grouped by the first part of their name (agent.nia.run → agent).
const OPS: Record<string, string> = { agent: "Agents and Nia", marketing: "Pictures and videos", other: "Other" };
const op = (key: string) => OPS[key] ?? nameOf(key);

export function MoneyPage() {
  const [days, setDays] = usePeriod();
  const { data, error } = useGroup("/admin-api/insights/money", days);
  if (error) return <Failed error={error} />;
  if (!data) return <Loading />;
  const x = data.series.days;
  const b = data.breakdowns;
  const calls = Object.fromEntries((b["ai.calls"] ?? []).map((i) => [i.dimension, i.value]));
  const failures = Object.fromEntries((b["ai.failures"] ?? []).map((i) => [i.dimension, i.value]));
  const latency = Object.fromEntries((b["ai.latency_p50_ms"] ?? []).map((i) => [i.dimension, i.value]));
  const ops = (b["ai.cost_usd"] ?? []).map((i) => i.dimension);
  for (const o of Object.keys(calls)) if (!ops.includes(o)) ops.push(o);
  const aiTable = {
    head: ["Feature", "Cost", "Calls", "Cost per call", "Failed", "Typical wait"],
    rows: ops.map((o) => {
      const cost = b["ai.cost_usd"]?.find((i) => i.dimension === o)?.value ?? 0;
      const n = calls[o] ?? 0;
      return [op(o), fmt(cost, "usd"), fmt(n), n ? fmt(cost / n, "usd") : "—", n ? fmt((failures[o] ?? 0) / n, "share") : "—", fmt(latency[o] ?? null, "ms")];
    }),
  };
  return (
    <>
      <FilterRow days={days} onDays={setDays} note="AI cost is the provider's estimate in US dollars." />
      <KpiGrid kpis={data.kpis} days={days} className="lg:grid-cols-5" />
      <div className="mt-4 grid items-start gap-4 lg:grid-cols-2">
        <MetricChart title="AI cost" subtitle="All AI features together" x={x} days={days} metric="ai.cost_usd" unit="usd" series={[{ key: "ai", label: "AI cost", values: values(data.series, "ai.cost_usd") }]} />
      </div>
      <Section title="AI by feature" description="What each AI feature costs and how reliable it is. A feature that fails often or waits long is a candidate for a cheaper model, a cache or a shorter prompt.">
        <div className="grid items-start gap-4 lg:grid-cols-[1fr_2fr]">
          <ChartCard title="Cost by feature" table={barTable(b["ai.cost_usd"] ?? [], ["Feature", "Cost"], "usd", op)}>
            <BarList items={b["ai.cost_usd"] ?? []} unit="usd" label={op} empty="No AI calls in this period." />
          </ChartCard>
          <ChartCard title="Every feature">
            {ops.length ? <DataTable table={aiTable} /> : <p className="py-6 text-center text-xs text-muted">No AI calls in this period.</p>}
          </ChartCard>
        </div>
      </Section>
    </>
  );
}

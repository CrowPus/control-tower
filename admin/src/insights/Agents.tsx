import { BarList, ChartCard, FilterRow, barTable } from "./charts";
import { useGroup, usePeriod, values } from "./data";
import { nameOf } from "./format";
import { Failed, KpiGrid, Loading, MetricChart, Section } from "./parts";

/** "nia:done" → "nia — done": runs are counted per agent and outcome. */
const run = (key: string) => key.split(":").map(nameOf).join(" — ");

export function AgentsPage() {
  const [days, setDays] = usePeriod();
  const { data, error } = useGroup("/admin-api/insights/agents", days);
  if (error) return <Failed error={error} />;
  if (!data) return <Loading />;
  const x = data.series.days;
  const b = data.breakdowns;
  return (
    <>
      <FilterRow days={days} onDays={setDays} note="Daily totals of the agents' work and your clearance decisions" />
      <KpiGrid kpis={data.kpis} days={days} />
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <MetricChart title="Agent runs per day" x={x} days={days} metric="agents.runs" series={[{ key: "r", label: "Runs", values: values(data.series, "agents.runs", "*") }]} />
        <MetricChart title="Proposals per day" subtitle="What the agents asked clearance for, and what you cleared" x={x} days={days} metric="clearance.proposed" series={[
          { key: "p", label: "Proposed", values: values(data.series, "clearance.proposed") },
          { key: "c", label: "Cleared", values: values(data.series, "clearance.decided", "approved") },
          { key: "x", label: "Rejected", values: values(data.series, "clearance.decided", "rejected") },
        ]} />
      </div>
      <Section title="By agent" description="Which agent works how often, how its runs end, and what its AI costs.">
        <div className="grid gap-4 lg:grid-cols-3">
          <ChartCard title="Runs by agent and outcome" table={barTable(b["agents.runs"] ?? [], ["Agent — outcome", "Runs"], "count", run)}>
            <BarList items={b["agents.runs"] ?? []} label={run} empty="No agent ran in this period." />
          </ChartCard>
          <ChartCard title="AI cost by agent" table={barTable(b["agents.cost_usd"] ?? [], ["Agent", "Cost"], "usd", nameOf)}>
            <BarList items={b["agents.cost_usd"] ?? []} unit="usd" label={nameOf} empty="No agent cost in this period." />
          </ChartCard>
          <ChartCard title="Your decisions" table={barTable(b["clearance.decided"] ?? [], ["Decision", "Proposals"], "count", nameOf)}>
            <BarList items={b["clearance.decided"] ?? []} label={nameOf} empty="Nothing decided in this period." />
          </ChartCard>
        </div>
      </Section>
    </>
  );
}

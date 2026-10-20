import { BarList, ChartCard, FilterRow, barTable } from "./charts";
import { useGroup, usePeriod, values } from "./data";
import { nameOf } from "./format";
import { Failed, KpiGrid, Loading, MetricChart, Section } from "./parts";

export function OperationsPage() {
  const [days, setDays] = usePeriod();
  const { data, error } = useGroup("/admin-api/insights/platform", days);
  if (error) return <Failed error={error} />;
  if (!data) return <Loading />;
  const x = data.series.days;
  const b = data.breakdowns;
  return (
    <>
      <FilterRow days={days} onDays={setDays} note="Daily totals; the Performance tab shows the last hours live" />
      <KpiGrid kpis={data.kpis} days={days} />
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <MetricChart title="Requests per day" x={x} days={days} metric="requests.count" series={[{ key: "r", label: "Requests", values: values(data.series, "requests.count", "*") }]} />
        <MetricChart title="Server errors per day" x={x} days={days} metric="requests.errors" series={[{ key: "e", label: "Server errors", values: values(data.series, "requests.errors", "*") }]} />
        <MetricChart title="Errors in browsers per day" subtitle="JavaScript errors reported by the admin app" x={x} days={days} metric="errors" series={[{ key: "b", label: "Browser errors", values: values(data.series, "errors", "*") }]} />
      </div>
      <Section title="Background work and messages" description="What the worker did and what was sent in this period, by outcome.">
        <div className="grid gap-4 lg:grid-cols-2">
          <ChartCard title="Background tasks" table={barTable(b.tasks ?? [], ["Outcome", "Tasks"], "count", nameOf)}>
            <BarList items={b.tasks ?? []} label={nameOf} empty="No tasks in this period." />
          </ChartCard>
          <ChartCard title="E-mails" table={barTable(b.email ?? [], ["Outcome", "E-mails"], "count", nameOf)}>
            <BarList items={b.email ?? []} label={nameOf} empty="No e-mails in this period." />
          </ChartCard>
        </div>
      </Section>
      <Section title="By part" description="Requests and the average daily p95 per part.">
        <div className="grid gap-4 lg:grid-cols-2">
          <ChartCard title="Requests" table={barTable(b["requests.count"] ?? [], ["Part", "Requests"], "count", nameOf)}>
            <BarList items={b["requests.count"] ?? []} label={nameOf} />
          </ChartCard>
          <ChartCard title="Speed (p95, average of days)" table={barTable(b["requests.p95_ms"] ?? [], ["Part", "p95"], "ms", nameOf)}>
            <BarList items={b["requests.p95_ms"] ?? []} unit="ms" label={nameOf} />
          </ChartCard>
        </div>
      </Section>
    </>
  );
}

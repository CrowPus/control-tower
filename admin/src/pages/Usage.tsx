import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, unwrap } from "../api";
import { Alert, Card, Empty, PageTitle, Select, Stat, Table, Td, usd } from "../ui";

export function UsagePage() {
  const [days, setDays] = useState(30);
  const { data, error } = useQuery({ queryKey: ["usage", days], queryFn: async () => unwrap(await api.GET("/admin-api/ai-usage", { params: { query: { days } } })) });
  if (error) return <Alert>{error.message}</Alert>;
  const rows = data?.days ?? [];
  const total = rows.reduce((sum, d) => sum + Number(d.cost_usd), 0);
  const calls = rows.reduce((sum, d) => sum + Number(d.calls), 0);
  const max = Math.max(0.0001, ...rows.map((d) => Number(d.cost_usd)));
  return (
    <>
      <PageTitle
        title="AI usage"
        description="What the AI costs, by day, model and feature. Estimated from token counts at the configured prices."
        actions={<Select className="w-36" value={String(days)} onChange={(e) => setDays(Number(e.target.value))} aria-label="Period"><option value="7">7 days</option><option value="30">30 days</option><option value="90">90 days</option></Select>}
      />
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
        <Stat label={`Spend, last ${days} days`} value={usd(total)} />
        <Stat label="Model calls" value={calls.toLocaleString("en-GB")} />
        <Stat label="Average per day" value={usd(rows.length ? total / days : 0)} />
      </div>
      <Card title="Spend per day (USD)" className="mt-4">
        {rows.length === 0 ? <Empty>No AI usage in this period.</Empty> : (
          <div className="flex h-40 items-end gap-1" role="img" aria-label="AI spend per day">
            {rows.map((d) => (
              <div key={String(d.day)} className="group relative flex-1">
                <div className="rounded-t bg-primary/80" style={{ height: `${(Number(d.cost_usd) / max) * 150 + 1}px` }} />
                <span className="pointer-events-none absolute -top-6 left-1/2 hidden -translate-x-1/2 whitespace-nowrap rounded bg-fg px-1.5 py-0.5 text-[11px] text-bg group-hover:block">{String(d.day)}: {usd(Number(d.cost_usd))}</span>
              </div>
            ))}
          </div>
        )}
      </Card>
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card title="By model" padded={false}>
          {(data?.by_model ?? []).length === 0 ? <Empty>—</Empty> : (
            <Table head={["Model", "Calls", "Spend"]}>
              {(data?.by_model ?? []).map((m) => <tr key={String(m.model)}><Td>{String(m.model)}</Td><Td className="tabular-nums">{Number(m.calls).toLocaleString("en-GB")}</Td><Td className="tabular-nums">{usd(Number(m.cost_usd))}</Td></tr>)}
            </Table>
          )}
        </Card>
        <Card title="By feature" padded={false}>
          {(data?.by_operation ?? []).length === 0 ? <Empty>—</Empty> : (
            <Table head={["Feature", "Calls", "Spend"]}>
              {(data?.by_operation ?? []).map((m) => <tr key={String(m.operation)}><Td>{String(m.operation)}</Td><Td className="tabular-nums">{Number(m.calls).toLocaleString("en-GB")}</Td><Td className="tabular-nums">{usd(Number(m.cost_usd))}</Td></tr>)}
            </Table>
          )}
        </Card>
      </div>
    </>
  );
}

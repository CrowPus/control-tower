import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, unwrap } from "../api";
import { Alert, Card, PageTitle, Stat, ago, usd } from "../ui";

export function OverviewPage() {
  const { data, error } = useQuery({ queryKey: ["overview"], queryFn: async () => unwrap(await api.GET("/admin-api/overview")), refetchInterval: 30_000 });
  if (error) return <Alert>{error.message}</Alert>;
  if (!data) return null;
  const switchesOn = Object.entries(data.switches).filter(([, on]) => on);
  return (
    <>
      <PageTitle title="Overview" description="The install at a glance: your agents, what waits for you, what the AI costs and the background work." />
      {switchesOn.length > 0 && (
        <div className="mb-4"><Alert tone="warning">Switches on: {switchesOn.map(([name]) => name.replace(/_/g, " ")).join(", ")}. <Link to="/platform" className="underline">Change</Link></Alert></div>
      )}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Agents" value={data.agents_total} hint={`${data.agents_on} switched on`} />
        <Stat label="Runs today" value={data.runs_today} />
        <Stat label="Waiting for your clearance" value={data.proposals_waiting} hint={<Link to="/clearance" className="text-primary">Clearance</Link>} tone={data.proposals_waiting ? "warning" : undefined} />
        <Stat label="Failed tasks (24 h)" value={data.tasks_failed_24h} hint={`${data.tasks_dead} dead · ${data.tasks_queued} queued`} tone={data.tasks_dead ? "danger" : data.tasks_failed_24h ? "warning" : undefined} />
        <Stat label="AI spend today" value={usd(data.ai_spend_today_usd)} hint={`Cap ${usd(data.ai_cap_daily_usd)} a day`} tone={data.ai_spend_today_usd >= data.ai_cap_daily_usd * 0.8 ? "warning" : undefined} />
        <Stat label="AI spend this month" value={usd(data.ai_spend_month_usd)} hint={`Cap ${usd(data.ai_cap_monthly_usd)} a month`} tone={data.ai_spend_month_usd >= data.ai_cap_monthly_usd * 0.8 ? "warning" : undefined} />
      </div>
      <div className="mt-4">
        <Card title="Background work">
          <dl className="space-y-2 text-[13px]">
            <div className="flex justify-between gap-3"><dt className="text-muted">Last task finished</dt><dd>{ago(data.last_task_finished_at)}</dd></div>
            <div className="flex justify-between gap-3"><dt className="text-muted">Queued now</dt><dd>{data.tasks_queued}</dd></div>
          </dl>
        </Card>
      </div>
    </>
  );
}

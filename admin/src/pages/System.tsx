import { useQuery } from "@tanstack/react-query";
import { api, unwrap } from "../api";
import { Alert, Badge, Card, PageTitle, Table, Td, ago } from "../ui";

export function SystemPage() {
  const { data, error } = useQuery({ queryKey: ["system"], queryFn: async () => unwrap(await api.GET("/admin-api/system")), refetchInterval: 15_000 });
  if (error) return <Alert>{error.message}</Alert>;
  if (!data) return null;
  const stale = (value: string | null | undefined, minutes: number) => !value || Date.now() - new Date(value).getTime() > minutes * 60_000;
  const checks: [string, boolean, string][] = [
    ["Database", data.database === "ok", `${data.database_engine} · ${data.execution_mode}`],
    ["Scheduler", !stale(data.scheduler_last_run_at, 20), `last run ${ago(data.scheduler_last_run_at)}`],
    ["Worker", !data.oldest_queued_at || !stale(data.oldest_queued_at, 15), data.oldest_queued_at ? `oldest waiting task queued ${ago(data.oldest_queued_at)}` : `nothing waiting · last task ${ago(data.last_task_finished_at)}`],
    ["Service mailbox", data.system_mail_configured, data.system_mail_configured ? "confirmation and reset e-mails can be sent" : "SMTP_* not configured"],
    ["AI key", data.ai_configured, data.ai_configured ? "configured" : "GEMINI_API_KEY missing"],
    ["Redis (live events between processes)", data.redis_configured, data.redis_configured ? "configured" : "not configured"],
  ];
  return (
    <>
      <PageTitle title="System health" description="Refreshes every 15 seconds." />
      <Card padded={false}>
        <Table head={["Check", "State", "Detail"]}>
          {checks.map(([name, ok, detail]) => (
            <tr key={name}><Td className="font-medium">{name}</Td><Td><Badge tone={ok ? "success" : "danger"}>{ok ? "OK" : "Attention"}</Badge></Td><Td className="text-muted">{detail}</Td></tr>
          ))}
        </Table>
      </Card>
      <Card title="Queues" className="mt-4" padded={false}>
        <Table head={["Queue", "Queued", "Running", "Failed", "Dead", "Succeeded"]}>
          {Object.entries(data.queues).sort().map(([name, d]) => (
            <tr key={name}><Td className="font-medium">{name}</Td>{["queued", "running", "failed", "dead", "succeeded"].map((s) => <Td key={s} className="tabular-nums">{d[s] ?? 0}</Td>)}</tr>
          ))}
        </Table>
      </Card>
    </>
  );
}

import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import type { Me } from "../App";
import { api, unwrap } from "../api";
import { useAction } from "../hooks";
import { Alert, Button, Card, Empty, Field, Input, PageTitle, Select, Table, Td, Toggle, ago, when } from "../ui";

const SWITCH_TEXT: Record<string, { label: string; description: string }> = {
  paused_all: { label: "Pause all automation", description: "No agent runs and no background work is done until this is off." },
};

export function PlatformPage({ me }: { me: Me }) {
  const owner = me.admin.role === "owner";
  const { data, error } = useQuery({ queryKey: ["platform"], queryFn: async () => unwrap(await api.GET("/admin-api/platform")) });
  const put = useAction(async (body: Record<string, unknown>) => unwrap(await api.PUT("/admin-api/platform", { body })), { invalidate: [["platform"], ["overview"]], done: () => "Saved." });
  const [banner, setBanner] = useState("");
  useEffect(() => setBanner(data?.maintenance_banner ?? ""), [data?.maintenance_banner]);
  if (error) return <Alert>{error.message}</Alert>;
  if (!data) return null;
  return (
    <>
      <PageTitle title="Platform" description={owner ? "Settings for the whole install. Changes take effect at once and are logged." : "Settings for the whole install. Only an owner can change them."} />
      {put.notice && <div className="mb-3"><Alert tone={put.notice.tone}>{put.notice.text}</Alert></div>}
      {!data.system_mail_configured && <div className="mb-3"><Alert tone="warning">The install's mailbox (SMTP_*) is not configured: admin invitations and alerts cannot be e-mailed.</Alert></div>}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Emergency switches">
          <p className="mb-1 text-xs text-muted">For everything at once.</p>
          {Object.entries(SWITCH_TEXT).map(([name, text]) => (
            <Toggle key={name} label={text.label} description={text.description} checked={data.switches[name] ?? false} disabled={!owner || put.busy} onChange={(next) => put.run({ switches: { [name]: next } })} />
          ))}
        </Card>
        <Card title="The install">
          <dl className="space-y-2 text-[13px]">
            <div className="flex justify-between gap-3"><dt className="text-muted">Public address</dt><dd className="break-all text-right">{data.public_base_url || "—"}</dd></div>
            <div className="flex justify-between gap-3"><dt className="text-muted">AI cap a day</dt><dd>${data.server_ai_cap_daily_usd.toFixed(2)}</dd></div>
            <div className="flex justify-between gap-3"><dt className="text-muted">AI cap a month</dt><dd>${data.server_ai_cap_monthly_usd.toFixed(2)}</dd></div>
          </dl>
          <p className="mt-2 text-xs text-muted">The caps are the last safety rail for all AI use (AI_DAILY_BUDGET_USD, AI_MONTHLY_BUDGET_USD). Each agent has its own budget in Team.</p>
          <form className="mt-3 flex items-end gap-2" onSubmit={(e) => { e.preventDefault(); put.run({ maintenance_banner: banner }); }}>
            <div className="min-w-0 flex-1"><Field label="Banner on every admin page" hint="Empty: none."><Input value={banner} maxLength={300} disabled={!owner} onChange={(e) => setBanner(e.target.value)} /></Field></div>
            {owner && <Button type="submit" loading={put.busy}>Save</Button>}
          </form>
        </Card>
      </div>
      <Schedules owner={owner} />
    </>
  );
}

function Schedules({ owner }: { owner: boolean }) {
  const { data } = useQuery({ queryKey: ["schedules"], queryFn: async () => unwrap(await api.GET("/admin-api/schedules")), refetchInterval: 30_000 });
  const patch = useAction(async ({ id, body }: { id: number; body: { enabled?: boolean; interval_seconds?: number } }) => unwrap(await api.PATCH("/admin-api/schedules/{job_id}", { params: { path: { job_id: id } }, body })), { invalidate: [["schedules"]], done: () => "Schedule saved." });
  const options = [300, 900, 1800, 3600, 7200, 21600, 43200, 86400];
  const label = (sec: number) => (sec < 3600 ? `${sec / 60} min` : `${sec / 3600} h`);
  return (
    <Card title="Schedules" className="mt-4" padded={false}>
      <p className="px-4 pt-3 text-xs text-muted">How often each background job runs.</p>
      {patch.notice && <div className="px-4 pt-2"><Alert tone={patch.notice.tone}>{patch.notice.text}</Alert></div>}
      {!data?.length ? <Empty>—</Empty> : (
        <Table head={["Job", "Every", "Last run", "Next run", "On"]}>
          {data.map((j) => (
            <tr key={j.id}>
              <Td><span className="font-medium">{j.name.replace(/_/g, " ")}</span><p className="text-xs text-muted">{j.task_type}</p></Td>
              <Td>
                <Select className="w-32" value={String(j.interval_seconds)} disabled={!owner} onChange={(e) => patch.run({ id: j.id, body: { interval_seconds: Number(e.target.value) } })}>
                  {options.map((s) => <option key={s} value={s}>{label(s)}</option>)}
                  {!options.includes(j.interval_seconds) && <option value={j.interval_seconds}>{j.interval_seconds}s</option>}
                </Select>
              </Td>
              <Td className="whitespace-nowrap text-muted">{ago(j.last_run_at)}{j.last_status ? ` · ${j.last_status}` : ""}</Td>
              <Td className="whitespace-nowrap text-muted">{j.enabled ? when(j.next_run_at) : "—"}</Td>
              <Td><input type="checkbox" aria-label={`Run ${j.name}`} checked={j.enabled} disabled={!owner} onChange={(e) => patch.run({ id: j.id, body: { enabled: e.target.checked } })} /></Td>
            </tr>
          ))}
        </Table>
      )}
    </Card>
  );
}

import { useQuery } from "@tanstack/react-query";
import { CircleAlert, CircleCheck, CircleX } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { api, unwrap } from "../api";
import { useAction } from "../hooks";
import { Alert, Button, Card, Input, ago, cn, when } from "../ui";
import { Failed, Loading, Note } from "./parts";

const RULES: { key: string; label: string; unit: "count" | "ms" | "share" | "minutes" | "days"; group: string }[] = [
  { key: "uptime_failures", label: "Failed checks in a row before an address counts as down", unit: "count", group: "Service" },
  { key: "p95_ms", label: "Slow: p95 over the last 10 minutes above", unit: "ms", group: "Service" },
  { key: "error_rate", label: "Server errors: share of requests above", unit: "share", group: "Service" },
  { key: "min_requests", label: "Speed and error rules stay quiet below this many requests", unit: "count", group: "Service" },
  { key: "queue_stuck_minutes", label: "Background task waiting longer than", unit: "minutes", group: "Service" },
  { key: "new_error_events", label: "New error type seen this often within an hour", unit: "count", group: "Apps" },
  { key: "crashes_per_hour", label: "Phone app crashes per hour", unit: "count", group: "Apps" },
  { key: "bounce_rate", label: "E-mail bounces: share of the last 24 hours above", unit: "share", group: "Apps" },
  { key: "min_emails", label: "Bounce rule stays quiet below this many e-mails", unit: "count", group: "Apps" },
  { key: "ai_budget_share", label: "AI spend today above this share of the daily cap", unit: "share", group: "Business" },
];
const UNIT: Record<string, string> = { ms: "ms", share: "%", minutes: "min", days: "days", count: "" };

function Settings({ owner }: { owner: boolean }) {
  const { data } = useQuery({ queryKey: ["insights", "settings"], queryFn: async () => unwrap(await api.GET("/admin-api/insights/settings")) });
  const [form, setForm] = useState<Record<string, string>>({});
  useEffect(() => {
    if (!data) return;
    const alerts = data.alerts ?? {};
    setForm(Object.fromEntries(RULES.map((r) => [r.key, String(r.unit === "share" ? +((alerts[r.key] ?? 0) * 100).toFixed(2) : (alerts[r.key] ?? 0))])));
  }, [data]);
  const save = useAction(async (alerts: Record<string, number>) => unwrap(await api.PUT("/admin-api/insights/settings", { body: { alerts } })), { invalidate: [["insights", "settings"]], done: () => "Saved. The next check uses the new values." });
  if (!data) return <Loading />;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.run(Object.fromEntries(RULES.map((r) => [r.key, Math.max(0, Number(form[r.key]) || 0) / (r.unit === "share" ? 100 : 1)])));
  };
  return (
    <Card title="When to warn you" actions={owner ? undefined : <span className="text-xs text-muted">Only the owner can change these</span>}>
      <form onSubmit={submit} className="space-y-5">
        {["Service", "Apps", "Business"].map((group) => (
          <fieldset key={group}>
            <legend className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">{group}</legend>
            <div className="grid max-w-3xl gap-y-2">
              {RULES.filter((r) => r.group === group).map((r) => (
                <label key={r.key} className="flex items-center justify-between gap-3 text-[13px] text-fg">
                  <span className="min-w-0">{r.label}</span>
                  <span className="flex shrink-0 items-center gap-1.5">
                    <Input type="number" min={0} step="any" className="w-24 text-right" value={form[r.key] ?? ""} disabled={!owner} onChange={(e) => setForm({ ...form, [r.key]: e.target.value })} />
                    <span className="w-8 text-xs text-muted">{UNIT[r.unit]}</span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
        ))}
        {save.notice && <Alert tone={save.notice.tone}>{save.notice.text}</Alert>}
        {owner && <Button type="submit" variant="primary" loading={save.busy}>Save</Button>}
      </form>
    </Card>
  );
}

export function AlertsPage({ owner }: { owner: boolean }) {
  const { data, error } = useQuery({ queryKey: ["insights", "alerts"], queryFn: async () => unwrap(await api.GET("/admin-api/insights/alerts", { params: { query: { days: 30 } } })), refetchInterval: 60_000 });
  const resolve = useAction(async (id: number) => unwrap(await api.POST("/admin-api/insights/alerts/{alert_id}/resolve", { params: { path: { alert_id: id } } })), {
    invalidate: [["insights", "alerts"], ["insights", "today"]],
    done: () => "Closed. It opens again if the problem is still there at the next check.",
  });
  return (
    <div className="space-y-6">
      <section>
        <p className="mb-3 max-w-3xl text-xs text-muted">The worker checks every 5 minutes. A new alert is e-mailed to every admin (critical ones start with DRINGEND) and appears in the bell; it closes by itself once the problem is gone, and you get one more e-mail then.</p>
        {resolve.notice && <div className="mb-3"><Alert tone={resolve.notice.tone}>{resolve.notice.text}</Alert></div>}
        {error ? <Failed error={error} /> : !data ? <Loading /> : !data.length ? <Note>No alerts in the last 30 days.</Note> : (
          <ul className="divide-y divide-border rounded-[var(--radius-card)] border border-border bg-surface">
            {data.map((a) => {
              const open = !a.resolved_at;
              const critical = a.severity === "critical";
              const Icon = !open ? CircleCheck : critical ? CircleX : CircleAlert;
              return (
                <li key={a.id} className="flex flex-wrap items-start gap-3 px-4 py-3">
                  <Icon className={cn("mt-0.5 h-4 w-4 shrink-0", !open ? "text-success" : critical ? "text-danger" : "text-warning")} aria-hidden />
                  <div className="min-w-0 flex-1">
                    <p className="text-[13px] text-fg">
                      <span className={cn("mr-2 text-xs font-medium", !open ? "text-success" : critical ? "text-danger" : "text-warning")}>{!open ? "Resolved" : critical ? "Critical" : "Warning"}</span>
                      {a.subject}
                    </p>
                    <p className="mt-0.5 text-xs text-muted">
                      Opened {when(a.opened_at)} · {open ? `last seen ${ago(a.last_seen_at)}` : `resolved ${ago(a.resolved_at)}`}
                      {Object.keys(a.detail).length > 0 && ` · ${Object.entries(a.detail).map(([k, v]) => `${k.replace(/_/g, " ")} ${typeof v === "number" ? +v.toFixed(3) : String(v)}`).join(", ")}`}
                    </p>
                  </div>
                  {open && owner && <Button size="sm" loading={resolve.busy && resolve.variables === a.id} onClick={() => resolve.run(a.id)}>Close</Button>}
                </li>
              );
            })}
          </ul>
        )}
      </section>
      <Settings owner={owner} />
    </div>
  );
}

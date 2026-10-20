import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, unwrap } from "../api";
import { useAction } from "../hooks";
import { Alert, Button, Card, Empty, PageTitle, Pager, Select, StatusBadge, Table, Td, ago } from "../ui";

const STATUSES = ["queued", "running", "failed", "dead", "succeeded", "cancelled"];

export function TasksPage() {
  const [status, setStatus] = useState("failed");
  const [queue, setQueue] = useState("");
  const [page, setPage] = useState(1);
  const key = ["tasks", status, queue, page];
  const { data, error } = useQuery({
    queryKey: key,
    queryFn: async () => unwrap(await api.GET("/admin-api/tasks", { params: { query: { status: status || undefined, queue: queue || undefined, page, page_size: 50 } } })),
    refetchInterval: 15_000,
    placeholderData: (previous) => previous,
  });
  const retry = useAction(async (id: number) => unwrap(await api.POST("/admin-api/tasks/{task_id}/retry", { params: { path: { task_id: id } } })), { invalidate: [["tasks"], ["overview"]] });
  const cancel = useAction(async (id: number) => unwrap(await api.POST("/admin-api/tasks/{task_id}/cancel", { params: { path: { task_id: id } } })), { invalidate: [["tasks"], ["overview"]] });
  const queues = Object.keys(data?.depths ?? {}).sort();
  return (
    <>
      <PageTitle title="Agents & queue" description="Background work: agent runs, mail, insights and maintenance. Payloads and results stay hidden: they can hold your data." />
      <div className="mb-4 grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-6">
        {queues.map((name) => {
          const d = data?.depths[name] ?? {};
          return (
            <button key={name} type="button" onClick={() => { setQueue(queue === name ? "" : name); setPage(1); }} className={`rounded-[var(--radius-card)] border px-3 py-2 text-left ${queue === name ? "border-primary bg-primary-soft" : "border-border bg-surface hover:bg-raised"}`}>
              <p className="text-xs font-medium text-fg">{name}</p>
              <p className="text-[11px] text-muted">{d.queued ?? 0} queued · {d.running ?? 0} running{(d.dead ?? 0) > 0 ? ` · ${d.dead} dead` : ""}</p>
            </button>
          );
        })}
      </div>
      <div className="mb-3 flex flex-wrap gap-2">
        <Select className="w-44" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }} aria-label="Status">
          <option value="">All statuses</option>
          {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
        </Select>
        {queue && <Button variant="ghost" onClick={() => setQueue("")}>Queue: {queue} ✕</Button>}
      </div>
      {[retry.notice, cancel.notice, error && { tone: "danger" as const, text: error.message }].map((n, i) => n && <div key={i} className="mb-2"><Alert tone={n.tone}>{n.text}</Alert></div>)}
      <Card padded={false}>
        {data && data.items.length === 0 ? <Empty>No tasks here.</Empty> : (
          <Table head={["Task", "Status", "Attempts", "When", "Error", ""]}>
            {(data?.items ?? []).map((t) => (
              <tr key={t.id}>
                <Td><span className="font-medium">{t.task_type}</span><p className="text-xs text-muted">#{t.id} · {t.queue}</p></Td>
                <Td><StatusBadge status={t.status} /></Td>
                <Td className="tabular-nums text-muted">{t.attempts}/{t.max_attempts}</Td>
                <Td className="whitespace-nowrap text-muted">{ago(t.finished_at ?? t.started_at ?? t.run_at)}</Td>
                <Td className="max-w-xs"><span className="line-clamp-2 break-words text-xs text-muted" title={t.last_error}>{t.last_error_code ? `${t.last_error_code}: ` : ""}{t.last_error || "—"}</span></Td>
                <Td className="whitespace-nowrap">
                  {["failed", "dead", "cancelled"].includes(t.status) && <Button size="sm" onClick={() => retry.run(t.id)} loading={retry.busy && retry.variables === t.id}>Retry</Button>}{" "}
                  {["queued", "failed", "dead"].includes(t.status) && <Button size="sm" variant="ghost" onClick={() => cancel.run(t.id)} loading={cancel.busy && cancel.variables === t.id}>Cancel</Button>}
                </Td>
              </tr>
            ))}
          </Table>
        )}
        {data && <Pager page={data.page} pageSize={data.page_size} total={data.total} onPage={setPage} />}
      </Card>
    </>
  );
}

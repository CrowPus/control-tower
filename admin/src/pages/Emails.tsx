import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, unwrap } from "../api";
import { Alert, Badge, Card, PageTitle, Table, Td, ago } from "../ui";

const VIEWS = [["problems", "Failed or returned"], ["retrying", "Waiting for a retry"], ["all", "All"]] as const;
const REASONS: Record<string, string> = {
  no_such_user: "Address does not exist",
  mailbox_full: "Mailbox full",
  blocked: "Blocked by the recipient's server",
  rejected: "Refused by the recipient's server",
};

// The service's e-mails of the last 30 days (services/mail_delivery.py): what failed or came back, so
// nobody who never got their link is lost unnoticed. Recipients, kinds and reasons; never the content.
export function EmailsPage() {
  const [view, setView] = useState<(typeof VIEWS)[number][0]>("problems");
  const { data, error } = useQuery({ queryKey: ["email-deliveries", view], queryFn: async () => unwrap(await api.GET("/admin-api/email-deliveries", { params: { query: { status: view } } })), refetchInterval: 60_000 });
  if (error) return <Alert>{error.message}</Alert>;
  const counts = data?.counts ?? {};
  return (
    <>
      <PageTitle title="E-mails" description="Every e-mail the service sends is recorded: retried while the mail server is busy, and marked when it comes back. Last 30 days." />
      <div className="mb-4 flex flex-wrap gap-2 text-[13px]">
        {(["sent", "retrying", "failed", "bounced"] as const).map((s) => (
          <span key={s} className="rounded-full border border-border px-3 py-1"><span className="text-muted">{s}</span> <strong className="tabular-nums">{counts[s] ?? 0}</strong></span>
        ))}
      </div>
      <div className="mb-3 flex gap-1">
        {VIEWS.map(([key, label]) => (
          <button key={key} type="button" onClick={() => setView(key)} className={`rounded-md px-3 py-1 text-[13px] ${view === key ? "bg-primary text-primary-fg" : "text-muted hover:text-fg"}`}>{label}</button>
        ))}
      </div>
      <Card padded={false}>
        <Table head={["When", "Kind", "To", "Status", "Why", "Attempts"]}>
          {(data?.items ?? []).map((row) => (
            <tr key={row.id}>
              <Td className="whitespace-nowrap text-muted">{ago(row.created_at)}</Td>
              <Td className="font-mono text-xs">{row.kind}</Td>
              <Td className="max-w-[260px] truncate"><span title={`${row.recipient} (${row.owner})`}>{row.recipient}</span></Td>
              <Td><Badge tone={row.status === "sent" ? "success" : row.status === "retrying" ? "warning" : row.status === "superseded" ? "neutral" : "danger"}>{row.status}</Badge></Td>
              <Td className="max-w-[360px] text-xs text-muted">{row.bounce_reason ? <><strong className="text-fg">{REASONS[row.bounce_reason] ?? row.bounce_reason}</strong><span className="block truncate" title={row.bounce_detail}>{row.bounce_detail}</span></> : row.last_error}</Td>
              <Td className="tabular-nums">{row.attempts}</Td>
            </tr>
          ))}
        </Table>
        {data && data.items.length === 0 && <p className="p-4 text-[13px] text-muted">Nothing here: every e-mail in this view went out.</p>}
      </Card>
    </>
  );
}

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, unwrap } from "../api";
import { Alert, Badge, Card, Empty, Input, PageTitle, Pager, Table, Td, when } from "../ui";

export function AdminLogPage() {
  const [action, setAction] = useState("");
  const [page, setPage] = useState(1);
  const { data, error } = useQuery({
    queryKey: ["audit", action, page],
    queryFn: async () => unwrap(await api.GET("/admin-api/audit", { params: { query: { action: action || undefined, page, page_size: 50 } } })),
    placeholderData: (previous) => previous,
  });
  return (
    <>
      <PageTitle title="Admin log" description="Every admin sign-in and change, oldest entries kept. Nobody can edit or delete it." />
      <Input className="mb-3 w-full sm:w-72" placeholder="Filter by action, e.g. team." value={action} onChange={(e) => { setAction(e.target.value); setPage(1); }} aria-label="Filter by action" />
      {error && <Alert>{error.message}</Alert>}
      <Card padded={false}>
        {data && data.items.length === 0 ? <Empty>No entries.</Empty> : (
          <Table head={["When", "Admin", "Action", "Target", "From", "Details"]}>
            {(data?.items ?? []).map((r) => (
              <tr key={r.id}>
                <Td className="whitespace-nowrap text-muted">{when(r.timestamp)}</Td>
                <Td>{r.admin_email || <span className="text-muted">—</span>}</Td>
                <Td><span className="font-medium">{r.action}</span>{r.result !== "ok" && <> <Badge tone="danger">{r.result}</Badge></>}</Td>
                <Td className="text-muted">{r.target_type ? `${r.target_type} ${r.target_id}` : "—"}</Td>
                <Td className="text-muted">{r.ip_address || "—"}</Td>
                <Td className="max-w-sm"><code className="line-clamp-2 break-all text-[11px] text-muted">{Object.keys(r.metadata).length ? JSON.stringify(r.metadata) : ""}</code></Td>
              </tr>
            ))}
          </Table>
        )}
        {data && <Pager page={data.page} pageSize={data.page_size} total={data.total} onPage={setPage} />}
      </Card>
    </>
  );
}
